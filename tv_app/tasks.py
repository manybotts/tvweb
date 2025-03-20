import re
import os
import time
import logging
import requests
import asyncio
from urllib.parse import quote_plus
from celery import Celery
from celery.utils.log import get_task_logger
from dotenv import load_dotenv
from redis import Redis
import telegram
from telegram.error import RetryAfter, TimedOut, NetworkError
from sqlalchemy.exc import OperationalError
from fuzzywuzzy import process, fuzz
from .models import db, TVShow  # Import your models
import hashlib
import unicodedata
from celery.schedules import crontab


load_dotenv()

# --- Celery Setup ---
celery = Celery(__name__, broker=os.environ.get('REDIS_URL'), backend=os.environ.get('REDIS_URL'))
logger = get_task_logger(__name__)

# --- Celery Beat Schedule ---
@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Calls update_tv_shows every 30 minutes.
    sender.add_periodic_task(crontab(minute='*/30'), update_tv_shows.s())

# --- TMDB API ---
TMDB_CALLS_PER_SECOND = 4
TMDB_PERIOD = 1

# --- Redis (for caching and locking) ---
redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')

# --- API Key Management ---
API_KEYS = [
    os.environ.get('API_KEY_1'),
    os.environ.get('API_KEY_2'),
    os.environ.get('API_KEY_3'),
    # Add more keys as needed
]
API_KEYS = [key for key in API_KEYS if key]  # Remove any None values
current_api_key_index = 0

# --- Helper Functions ---

def get_tmdb_data(url, params=None):
    """Fetches data from TMDB, handling API key rotation."""
    global current_api_key_index

    if params is None:
        params = {}

    for _ in range(len(API_KEYS)):  # Try each key
        params['api_key'] = API_KEYS[current_api_key_index]
        response = requests.get(url, params=params, timeout=10)  # Added timeout

        if response.status_code == 200:
            return response.json()
        elif response.status_code in (429, 401):  # Rate limit or unauthorized
            logger.warning(f"API Key {API_KEYS[current_api_key_index][:4]}... failed (status {response.status_code}). Trying next key...")
            current_api_key_index = (current_api_key_index + 1) % len(API_KEYS)  # Cycle
        else:
            logger.error(f"TMDB API error: {response.status_code} - {response.text}")
            return None

    logger.error("All API keys failed.")
    return None

def get_tmdb_id_by_title(show_title, language='en-US'):
    """Gets TMDB ID, using caching and prioritizing exact/popular matches."""
    cache_key = f"tmdb_id:{show_title}:{language}"
    tmdb_id = redis_client.get(cache_key)
    if tmdb_id:
        return int(tmdb_id)  # Return as integer

    search_url = "https://api.themoviedb.org/3/search/tv"
    params = {"query": show_title, "language": language, "include_adult": "false"}  # Add include_adult
    data = get_tmdb_data(search_url, params)

    if data and data.get('results'):
        results = data['results']

        # 1. Exact Match (Case-Insensitive)
        for result in results:
            if result['name'].lower() == show_title.lower():
                tmdb_id = result['id']
                logger.info(f"Direct match found: {result['name']} (ID: {tmdb_id})")
                redis_client.setex(cache_key, 604800, tmdb_id)  # Cache
                return tmdb_id

        # 2. Prioritize Popularity (if no exact match)
        if results:
            most_popular = max(results, key=lambda x: x.get('popularity', 0))
            best_match, score = process.extractOne(show_title, [result['name'] for result in results])
            if score >= 80 and best_match == most_popular['name']:  # Use name from most_popular
                tmdb_id = most_popular['id']
                logger.info(f"Fuzzy match (high popularity): {most_popular['name']} (ID: {tmdb_id}, Score: {score})")
                redis_client.setex(cache_key, 604800, tmdb_id)
                return tmdb_id
            else:
                logger.warning(f"Fuzzy match score too low ({score}) or not most popular for: {show_title}")
                return None
    else:
        logger.warning(f"No results found in TMDB for show: {show_title}")
        return None

def get_trailer(tmdb_id):
    tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/videos"
    data = get_tmdb_data(tmdb_url)
    if data and data.get('results'):
        for result in data['results']:
            if result['type'] == 'Trailer' and result['site'] == 'YouTube':
                return result['key']  # Return the key, not the full URL
    return None
def parse_telegram_post(text):
    match = re.search(r"^(?!#|_#).*S(\d{2})E(\d{2})\s*(.*?)\s*-\s*(https?://\S+)", text, re.MULTILINE | re.IGNORECASE)
    if match:
        return {
            'show_name': match.group(3).strip(),
            'season': int(match.group(1)),
            'episode': int(match.group(2)),
            'download_link': match.group(4)
        }
    return None

async def fetch_new_telegram_posts(bot):
    channel_id = int(os.environ.get('TELEGRAM_CHANNEL_ID'))
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID not set!")
        return []
    last_update_id = redis_client.get('last_telegram_update_id')
    last_update_id = int(last_update_id) if last_update_id else None
    new_posts = []
    try:
        updates = await bot.get_updates(offset=last_update_id + 1 if last_update_id else None, allowed_updates=[telegram.Update.MESSAGE], timeout=60)
        for update in updates:
            if update.message and update.message.chat_id == channel_id and update.message.caption:
                 new_posts.append(update.message)
            redis_client.set('last_telegram_update_id', update.update_id)
    except NetworkError as e:
        logger.error(f'NetworkError: {e}')
    except RetryAfter as e:
        logger.warning(f"Rate limit exceeded. Retrying after {e.retry_after} seconds.")
        time.sleep(e.retry_after)
    except TimedOut as e:
        logger.error(f"Telegram API request timed out: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error fetching updates: {e}")
    return new_posts


def calculate_content_hash(show_name, season_number, episode_number, download_link):
    """Calculates a SHA-256 hash, now including season and episode."""
    content_string = f"{show_name}-{season_number}-{episode_number}-{download_link}"
    return hashlib.sha256(content_string.encode('utf-8')).hexdigest()

def normalize_string(input_string):
    """Normalizes strings for consistent comparison (lowercase, no special chars)."""
    if input_string is None:
        return ""
    text = input_string.lower()
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C')  # Remove control characters
    text = re.sub(r'[^\w\s]', '', text)  # Remove punctuation, keep spaces
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    return text

@celery.task(bind=True, retry_backoff=True, max_retries=5) # Added max_retries
def update_tv_shows(self):
    """Fetches new Telegram posts, parses them, and updates the database."""
    logger.info("Starting update_tv_shows task...")
    lock_id = "update_tv_shows_lock"
    lock = redis_client.lock(lock_id, timeout=600)

    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return

    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        posts = asyncio.run(fetch_new_telegram_posts(bot))

        if not posts:
            logger.info("No new Telegram posts found.")
            return

        from tv_app.app import app
        with app.app_context():
            for post in posts:
                post_data = parse_telegram_post(post.caption)
                if not post_data:
                    continue

                show_name, season_number, episode_number, download_link = (
                    post_data['show_name'],
                    post_data['season'],
                    post_data['episode'],
                    post_data['download_link']
                )

                content_hash = calculate_content_hash(show_name, season_number, episode_number, download_link)
                if redis_client.sismember("processed_posts", content_hash):
                    continue

                normalized_show_name = normalize_string(show_name)
                show = TVShow.query.filter(func.lower(TVShow.show_name) == normalized_show_name).first()

                if show:
                    existing_episode = TVShow.query.filter_by(show_id=show.id, season_range=season_number, episode_number=episode_number).first()

                    if not existing_episode:
                        new_episode = TVShow(episode_title=None, episode_number=episode_number, season_range=season_number, show_id=show.id, download_link=download_link, overview=None, content_hash=content_hash)
                        db.session.add(new_episode)
                        if season_number == 1 and episode_number == 1 and not show.content_hash:
                            tmdb_id = get_tmdb_id_by_title(show.show_name)
                            if tmdb_id:
                                tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=en-US"
                                show_details = get_tmdb_data(tmdb_url)
                                if show_details:
                                    show.overview = show_details.get('overview')
                                    show.genre = ', '.join([genre['name'] for genre in show_details.get('genres', [])])
                                    show.poster_path = f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None
                                    show.vote_average = show_details.get('vote_average')
                                    show.content_hash = content_hash
                                    show.year = int(show_details.get('first_air_date', '0000-00-00').split('-')[0]) if show_details.get('first_air_date') else None
                                    show.season_range = show_details.get('number_of_seasons', 1)
                                    db.session.commit()
                    else:
                         existing_episode.download_link = download_link
                         existing_episode.content_hash = content_hash
                         db.session.commit()

                else:
                    tmdb_id = get_tmdb_id_by_title(show_name)
                    if tmdb_id:
                        tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=en-US"
                        show_details = get_tmdb_data(tmdb_url)
                        if show_details:
                            new_show = TVShow(
                                show_name=show_details.get('name'),
                                overview=show_details.get('overview'),
                                year=int(show_details.get('first_air_date', '0000-00-00').split('-')[0]) if show_details.get('first_air_date') else None,
                                genre=', '.join([genre['name'] for genre in show_details.get('genres', [])]),
                                poster_path=f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None,
                                vote_average=show_details.get('vote_average'),
                                content_hash= content_hash,
                                download_link=None,
                                season_range=show_details.get('number_of_seasons', 1)
                            )
                            db.session.add(new_show)
                            db.session.commit()
                            new_episode = TVShow(episode_title=None, episode_number=episode_number, season_range=season_number, show_id=new_show.id, download_link=download_link, overview=None, content_hash=content_hash)
                            db.session.add(new_episode)
                            db.session.commit()
                redis_client.sadd("processed_posts", content_hash)
                try:
                    db.session.commit()
                except OperationalError as e:
                    db.session.rollback()
                    self.retry(exc=e, countdown=60)
                except Exception as e:
                    db.session.rollback()
                    self.retry(exc=e, countdown=60)
    except Exception as e:
        logger.exception(f"Unexpected error in update_tv_shows: {e}")
        self.retry(exc=e, countdown=120)
    finally:
        lock.release()
