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
from .models import db, Show, Episodes  # Corrected import
from sqlalchemy import func

load_dotenv()

# --- Celery Setup ---
celery = Celery(__name__, broker=os.environ.get('REDIS_URL'), backend=os.environ.get('REDIS_URL'))
logger = get_task_logger(__name__)

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
        elif response.status_code == 429 or response.status_code == 401:  # Rate limit or unauthorized
            logger.warning(f"API Key {API_KEYS[current_api_key_index][:4]}... failed (status {response.status_code}). Trying next key...")
            current_api_key_index = (current_api_key_index + 1) % len(API_KEYS)  # Cycle
        else:
            # Handle other errors (e.g., 500 Internal Server Error)
            logger.error(f"TMDB API error: {response.status_code} - {response.text}")
            return None  # Or raise an exception

    logger.error("All API keys have failed.")
    return None

def get_tmdb_id_by_title(show_title, language='en-US'):
    """Gets TMDB ID, using caching and prioritizing exact/popular matches."""
    cache_key = f"tmdb_id:{show_title}:{language}"
    tmdb_id = redis_client.get(cache_key)
    if tmdb_id:
        return int(tmdb_id)

    search_url = "https://api.themoviedb.org/3/search/tv"
    params = {"query": show_title, "language": language, "include_adult": "false"}
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
            if score >= 80 and best_match == most_popular['name']:
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
                return result['key']
    return None

def parse_telegram_post(text):
    """
    Parses a Telegram post, extracting show name, season, episode, and link.
    Excludes lines starting with '#' or '#_'.  Returns a dictionary or None.
    """
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
    """Fetches new posts from Telegram, handling errors and respecting rate limits."""

    channel_id = TELEGRAM_CHANNEL_ID
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID environment variable not set!")
        return []

    last_update_id = redis_client.get('last_telegram_update_id')
    last_update_id = int(last_update_id) if last_update_id else None
    new_posts = []

    try:
        updates = await bot.get_updates(offset=last_update_id + 1 if last_update_id else None, allowed_updates=[telegram.Update.MESSAGE], timeout=60)
        for update in updates:
            if update.message and update.message.chat_id == int(channel_id) and update.message.caption:
                new_posts.append(update.message)
            redis_client.set('last_telegram_update_id', update.update_id)

    except NetworkError as e:
        logger.error(f"Network error fetching Telegram updates: {e}")
    except RetryAfter as e:
        logger.warning(f"Rate limit exceeded. Retrying after {e.retry_after} seconds.")
        time.sleep(e.retry_after)  # synchronous sleep is OK *here*
    except TimedOut as e:
        logger.error(f"Telegram API request timed out: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error fetching Telegram updates: {e}")

    return new_posts

@celery.task(bind=True, retry_backoff=True, max_retries=5)
def update_tv_shows(self):
    """Fetches Telegram posts, parses them, and updates the database."""

    logger.info("Starting update_tv_shows task...")
    lock = redis_client.lock("update_tv_shows_lock", timeout=600)

    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return

    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        posts =  asyncio.run(fetch_new_telegram_posts(bot))

        for post in posts:
            post_data = parse_telegram_post(post.caption)

            if post_data:
                show_name = post_data['show_name']
                season_number = post_data['season']
                episode_number = post_data['episode']
                download_link = post_data['download_link']
                logger.info(f"Parsed: {show_name} S{season_number:02d}E{episode_number:02d}")

                show = Show.query.filter(func.lower(Show.title) == func.lower(show_name)).first()

                if show:
                    logger.info(f"Show '{show_name}' found (ID: {show.id}).")
                    existing_episode = Episodes.query.filter_by(show_id=show.id, season_number=season_number, episode_number=episode_number).first()

                    if not existing_episode:
                        logger.info(f"Adding episode S{season_number:02d}E{episode_number:02d} for {show_name}")
                        new_episode = Episodes(title = None, episode_number=episode_number, season_number=season_number, show_id=show.id, download_link=download_link, overview = None)
                        db.session.add(new_episode)

                        if season_number == 1 and episode_number == 1 and not show.imdb_id:
                            tmdb_id = get_tmdb_id_by_title(show.title)
                            if tmdb_id:
                                tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=en-US"
                                show_details = get_tmdb_data(tmdb_url)
                                if show_details:
                                    show.overview = show_details.get('overview')
                                    show.genre = ', '.join([genre['name'] for genre in show_details.get('genres', [])])
                                    show.image_url = f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None
                                    show.trailer_url = f"https://www.youtube.com/watch?v={get_trailer(tmdb_id)}" if get_trailer(tmdb_id) else None
                                    show.imdb_id = str(tmdb_id)
                                    show.available_seasons = show_details.get('number_of_seasons', 1)
                    else:
                        logger.info(f"Episode S{season_number:02d}E{episode_number:02d} of '{show_name}' already exists.")
                else:
                    logger.info(f"Show '{show_name}' not found.  Fetching from TMDB...")
                    tmdb_id = get_tmdb_id_by_title(show_name)
                    if tmdb_id:
                        tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=en-US"
                        show_details = get_tmdb_data(tmdb_url)

                        if show_details:
                            new_show = Show(
                                title=show_details.get('name'),
                                overview=show_details.get('overview'),
                                release_year=int(show_details.get('first_air_date', '0000-00-00').split('-')[0]) if show_details.get('first_air_date') else None,
                                genre=', '.join([genre['name'] for genre in show_details.get('genres', [])]),
                                image_url=f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None,
                                trailer_url=f"https://www.youtube.com/watch?v={get_trailer(tmdb_id)}" if get_trailer(tmdb_id) else None,
                                imdb_id=str(tmdb_id),
                                download_link=None,  # The show itself has no direct download link
                                available_seasons = show_details.get('number_of_seasons', 1)
                            )
                            db.session.add(new_show)
                            db.session.flush() # To get the new_show.id
                            new_episode = Episodes(
                                title = None,
                                episode_number=episode_number,
                                season_number=season_number,
                                show_id=new_show.id,
                                download_link=download_link,
                                overview = None,
                            )
                            db.session.add(new_episode)

                        else:
                            logger.warning(f"Could not retrieve details for show ID {tmdb_id} from TMDB.")
                    else:
                        logger.warning(f"Could not find TMDB ID for show: {show_name}")

                try:
                    db.session.commit()
                    logger.info("Changes committed to the database.")
                except OperationalError as e:
                    db.session.rollback()
                    logger.exception(f"Database operational error: {e}")
                    self.retry(exc=e, countdown=60)
                except Exception as e:
                    db.session.rollback()
                    logger.exception(f"An error occurred: {e}")
                    self.retry(exc=e, countdown=60) # Keep the same retry
        finally:
            lock.release()
            logger.info("update_tv_shows task finished.")

    else:
        logger.info("update_tv_shows task is already running. Skipping.")
