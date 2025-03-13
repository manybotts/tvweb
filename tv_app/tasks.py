import os
import re
import requests
import asyncio
import logging
import json
import difflib
import psycopg2
from datetime import datetime, timezone
from urllib.parse import quote_plus
from dotenv import load_dotenv
from redis import Redis
from celery import Celery
from celery.exceptions import MaxRetriesExceededError
from pyrogram import Client, errors
from ratelimit import limits, sleep_and_retry

# Load environment variables
load_dotenv()

# Logging configuration
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Celery Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
celery = Celery(__name__, broker=REDIS_URL, backend=REDIS_URL)
celery.conf.timezone = "UTC"
celery.conf.enable_utc = True
celery.conf.beat_schedule = {
    "update-tv-shows-every-15-minutes": {
        "task": "tv_app.tasks.update_tv_shows",
        "schedule": 15 * 60,
    },
}

# TMDb API Rate Limits
CALLS = 30
PERIOD = 9

# Redis Client
redis_client = Redis.from_url(REDIS_URL, decode_responses=True)

# Telegram API Config
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
channel_id = int(os.getenv("TELEGRAM_CHANNEL_ID"))

# Pyrogram Client
pyrogram_client = Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# PostgreSQL Connection
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    """Establishes a new database connection."""
    return psycopg2.connect(DATABASE_URL)

async def fetch_telegram_posts():
    """Fetches new messages from the Telegram channel using Pyrogram."""
    logger.info(f"Fetching updates from Telegram channel: {channel_id}")

    posts = []
    try:
        async with pyrogram_client:
            updates = await pyrogram_client.get_updates()
            if not updates:
                logger.info("No new updates received.")
                return []

            latest_update_id = 0  # Track last processed update_id

            for update in updates:
                if update.message and update.message.chat.id == channel_id:
                    post_data = {
                        "caption": update.message.caption or "",
                        "message_id": update.message.message_id,
                    }
                    posts.append(post_data)
                    logger.info(f"Processed message ID: {update.message.message_id}")

                    latest_update_id = max(latest_update_id, update.update_id)

            if latest_update_id:
                await pyrogram_client.get_updates(offset=latest_update_id + 1)

    except errors.FloodWait as e:
        logger.warning(f"Rate-limited by Telegram. Sleeping for {e.value} seconds.")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.exception(f"Unexpected error fetching Telegram posts: {e}")

    logger.info(f"Total posts fetched: {len(posts)}")
    return posts

def fetch_telegram_posts_sync():
    """Sync wrapper to fetch Telegram posts."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(fetch_telegram_posts())

def parse_telegram_post(post):
    """Parses a Telegram post to extract show details."""
    try:
        text = post["caption"]
        lines = text.splitlines()
        show_name = lines[0].strip() if lines else None
        season_episode = lines[1].strip() if len(lines) > 1 and not lines[1].startswith("#_") else None
        download_link = None

        # Extract URL from caption
        url_match = re.search(r"https?://[^\s]+", text)
        if url_match:
            download_link = url_match.group(0)

        if show_name:
            return {
                "show_name": preprocess_show_name(show_name),
                "season_episode": season_episode,
                "download_link": download_link,
                "message_id": post["message_id"],
            }
        return None

    except Exception as e:
        logger.exception(f"Error parsing post: {e}")
        return None

def preprocess_show_name(name):
    """Cleans and standardizes show names."""
    return re.sub(r"[].*?[]|\b(hd|4k|2k|fhd|s\d+|e\d+)\b", "", name, flags=re.IGNORECASE).strip()

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
def fetch_tmdb_data(show_name, language="en-US"):
    """Fetches TV show metadata from TMDb API using fuzzy matching."""
    show_name = preprocess_show_name(show_name)
    cache_key = f"tmdb_data:{show_name.lower()}:{language}"

    if redis_client:
        cached_data = redis_client.get(cache_key)
        if cached_data:
            return json.loads(cached_data)

    headers = {
        "Authorization": f"Bearer {os.getenv('TMDB_BEARER_TOKEN')}",
        "Content-Type": "application/json"
    }
    search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"

    try:
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data.get("results"):
            best_match = max(
                search_data["results"],
                key=lambda x: difflib.SequenceMatcher(None, show_name.lower(), x["name"].lower()).ratio(),
                default=None,
            )

            if best_match:
                details_url = f"https://api.themoviedb.org/3/tv/{best_match['id']}?language={language}"
                details_response = requests.get(details_url, headers=headers, timeout=10)
                details_response.raise_for_status()
                details_data = details_response.json()

                data_to_cache = {
                    "poster_path": f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}"
                    if details_data.get("poster_path") else None,
                    "overview": details_data.get("overview"),
                    "vote_average": details_data.get("vote_average"),
                }
                if redis_client:
                    redis_client.setex(cache_key, 7 * 24 * 60 * 60, json.dumps(data_to_cache))

                return data_to_cache

    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb API error: {e}")

    return {}

@celery.task(bind=True, retry_backoff=5, max_retries=3)
def update_tv_shows(self):
    """Fetches Telegram posts, retrieves TMDb data, and updates PostgreSQL."""
    logger.info("Starting update_tv_shows task...")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        posts = fetch_telegram_posts_sync()

        for post in posts:
            parsed_data = parse_telegram_post(post)
            if parsed_data:
                tmdb_data = fetch_tmdb_data(parsed_data["show_name"]) or {}

                cursor.execute(
                    """
                    INSERT INTO tv_shows (show_name, episode_title, download_link, message_id, poster_path, overview, vote_average)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO UPDATE SET
                        episode_title = EXCLUDED.episode_title,
                        download_link = EXCLUDED.download_link,
                        poster_path = COALESCE(EXCLUDED.poster_path, tv_shows.poster_path),
                        overview = COALESCE(EXCLUDED.overview, tv_shows.overview),
                        vote_average = COALESCE(EXCLUDED.vote_average, tv_shows.vote_average);
                    """,
                    (parsed_data["show_name"], parsed_data["season_episode"], parsed_data["download_link"],
                     parsed_data["message_id"], tmdb_data.get("poster_path"), tmdb_data.get("overview"), tmdb_data.get("vote_average"))
                )
        conn.commit()

    except Exception as e:
        conn.rollback()
        logger.error(f"Database update failed: {e}")

    finally:
        cursor.close()
        conn.close()
