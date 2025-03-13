from celery import Celery
from celery.schedules import crontab
import os
import re
import requests
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
from redis import Redis
import asyncio
from ratelimit import limits, sleep_and_retry, RateLimitException
import difflib
import json
from pyrogram import Client, errors

load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Celery Configuration
celery = Celery(
    __name__, 
    broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), 
    backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
)
celery.conf.timezone = 'UTC'
celery.conf.enable_utc = True
celery.conf.beat_schedule = {
    'update-tv-shows-every-1-minute': {
        'task': 'update_tv_shows',
        'schedule': crontab(minute='*/1'),  # Every minute (for testing)
    },
}

# Rate limit and Redis setup
CALLS = 30
PERIOD = 9
redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)

# Telegram API credentials
api_id = int(os.environ.get("API_ID"))
api_hash = os.environ.get("API_HASH")
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
channel_id = int(os.environ.get('TELEGRAM_CHANNEL_ID'))

# Pyrogram Client
pyrogram_client = Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

async def fetch_telegram_posts():
    """Fetch new posts from Telegram using Pyrogram."""
    logger.info(f"Fetching updates from Telegram channel: {channel_id}")
    posts = []
    try:
        async with pyrogram_client:
            async for message in pyrogram_client.get_chat_history(chat_id=channel_id):
                if message.caption:
                    posts.append(message)
                    logger.debug(f"Added post: {message.id}")
    except errors.FloodWait as e:
        logger.warning(f"FloodWait error: Waiting for {e.value} seconds.")
        await asyncio.sleep(e.value)
        return await fetch_telegram_posts()
    except Exception as e:
        logger.exception(f"Error fetching posts: {e}")
    logger.info(f"Total posts fetched: {len(posts)}")
    return posts

def parse_telegram_post(post):
    """Parses a Telegram post to extract show details."""
    try:
        text = post.caption.strip()
        lines = text.splitlines()
        show_name, season_episode, download_link = None, None, None

        if len(lines) >= 3:
            show_name = preprocess_show_name(lines[0].strip())
            season_episode = None if lines[1].strip().startswith('#_') else lines[1].strip()

            for entity in post.caption_entities or []:
                if entity.type == 'text_link' and "click here" in text.lower():
                    download_link = entity.url
                    break

        return {
            'show_name': show_name,
            'season_episode': season_episode,
            'download_link': download_link,
            'message_id': post.id,
        } if show_name else None
    except Exception as e:
        logger.exception(f"Error parsing post: {e}")
        return None

def preprocess_show_name(name):
    """Cleans up the show name."""
    name = re.sub(r"(?i)\s*(season finale|new episodes|original series|tv series|limited series)\s*", "", name)
    name = re.sub(r"\s*\d{4}$", "", name)
    name = re.sub(r"\s*\d{4}$", "", name)
    name = name.replace("&", "and").replace("  ", " ")
    name = re.sub(r'[].*?[]', '', name)
    return name.strip()

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb with rate limiting and caching."""
    show_name = preprocess_show_name(show_name)
    cache_key = f"tmdb_data:{show_name.lower()}:{language}"
    
    if cached_data := redis_client.get(cache_key):
        return json.loads(cached_data)

    headers = {
        "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",
        "Content-Type": "application/json"
    }

    search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
    try:
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        results = search_response.json().get('results', [])

        if results:
            best_match = get_close_matches_with_threshold(show_name, [r['name'] for r in results], n=1, cutoff=0.6)
            show_id = next((r['id'] for r in results if r['name'] == best_match[0]), results[0]['id'])

            details_response = requests.get(f"https://api.themoviedb.org/3/tv/{show_id}?language={language}", headers=headers, timeout=10)
            details_response.raise_for_status()
            details = details_response.json()

            tmdb_data = {
                'poster_path': f"https://image.tmdb.org/t/p/w500{details.get('poster_path')}" if details.get('poster_path') else None,
                'overview': details.get('overview'),
                'vote_average': details.get('vote_average'),
            }
            redis_client.setex(cache_key, 7 * 24 * 60 * 60, json.dumps(tmdb_data))
            return tmdb_data
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb request error: {e}")
    return None

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    """Updates the database with new TV show info from Telegram."""
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=120, blocking_timeout=5)

    if not lock.acquire(blocking=False):
        logger.info("Task already running, skipping execution.")
        return

    try:
        posts = asyncio.run(fetch_telegram_posts())
        if not posts:
            return

        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow

            new_shows = []
            for post in posts:
                if parsed_data := parse_telegram_post(post):
                    tmdb_data = fetch_tmdb_data(parsed_data['show_name'])
                    show_data = {**parsed_data, **(tmdb_data or {})}

                    existing_show = TVShow.query.filter_by(message_id=parsed_data['message_id']).first()
                    if existing_show:
                        for key, value in show_data.items():
                            setattr(existing_show, key, value)
                    else:
                        new_shows.append(TVShow(**show_data))

            if new_shows:
                db.session.bulk_save_objects(new_shows)
            db.session.commit()
    except Exception as e:
        logger.exception(f"Error updating TV shows: {e}")
        self.retry(exc=e, countdown=min(300, (self.request.retries + 1) * 60))
    finally:
        lock.release()
