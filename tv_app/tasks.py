import re
import os
import time
import logging
import requests
import asyncio
from urllib.parse import quote_plus  # Not used, but good practice to keep if you might use it
from celery import Celery
from celery.utils.log import get_task_logger
from dotenv import load_dotenv
from redis import Redis
import telegram
from telegram.error import RetryAfter, TimedOut, NetworkError
from sqlalchemy.exc import OperationalError
from thefuzz import process, fuzz
from .models import db, Show, Episodes  # Import your models
from sqlalchemy import func
import json  # JSON import

load_dotenv()

# --- Celery Setup ---
celery = Celery(__name__, broker=os.environ.get('REDIS_URL'), backend=os.environ.get('REDIS_URL'))
logger = get_task_logger(__name__)

# --- TMDB API ---
TMDB_CALLS_PER_SECOND = 4  # Consider using this and time.sleep() for precise rate limiting if needed
TMDB_PERIOD = 1  #  Consider using this and time.sleep() for precise rate limiting if needed

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
        try:
            response = requests.get(url, params=params, timeout=10)  # Added timeout
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)

            if response.status_code == 200:
                return response.json()
        except requests.exceptions.RequestException as e:
             logger.error(f"Request failed: {e}") #General Request exception.

        if response.status_code == 429 or response.status_code == 401 :
            logger.warning(f"API Key index {current_api_key_index} failed (status {response.status_code}). Trying next key...")
            current_api_key_index = (current_api_key_index + 1) % len(API_KEYS)
        else:
            logger.error(f"TMDB API error: {response.status_code} - {response.text}")
            return None


    logger.error("All API keys have failed.")
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
                redis_client.setex(cache_key, 604800, str(tmdb_id))  # Cache, store as string
                return tmdb_id

        # 2. Prioritize Popularity (if no exact match)
        if results:
            most_popular = max(results, key=lambda x: x.get('popularity', 0))
            best_match, score = process.extractOne(show_title, [result['name'] for result in results], scorer=fuzz.token_sort_ratio) # Added a scorer for better results
            if score >= 80 and best_match == most_popular['name']:  # Use name from most_popular
                tmdb_id = most_popular['id']
                logger.info(f"Fuzzy match (high popularity): {most_popular['name']} (ID: {tmdb_id}, Score: {score})")
                redis_client.setex(cache_key, 604800, str(tmdb_id))  # Always store as string
                return tmdb_id
            else:
                logger.warning(f"Fuzzy match score too low ({score}) or not most popular for: {show_title}")
                return None
    else:
        logger.warning(f"No results found in TMDB for show: {show_title}")
        return None

def get_trailer(tmdb_id):
    """Gets the YouTube trailer key for a show."""
    tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/videos"
    data = get_tmdb_data(tmdb_url)
    if data and data.get('results'):
        for result in data['results']:
            if result['type'] == 'Trailer' and result['site'] == 'YouTube':
                return result['key']  # Return the key, not the full URL
    return None

def parse_telegram_post(text):
    """
    Parses a Telegram post text to extract show name, season, episode, and download link.
    Returns a dictionary with the extracted data, or None if parsing fails.
    Excludes lines starting with '#' or '#_'.
    """
    match = re.search(r"^(?!#|_#).*S(\d{2})E(\d{2})\s*(.*?)\s*-\s*(https?://\S+)", text, re.MULTILINE | re.IGNORECASE)

    if match:
        season = int(match.group(1))
        episode = int(match.group(2))
        show_name = match.group(3).strip()  # Extract and strip whitespace
        download_link = match.group(4)

        return {
            'show_name': show_name,
            'season': season,
            'episode': episode,
            'download_link': download_link
        }
    return None

async def fetch_new_telegram_posts(bot):
    """Fetches new posts from the specified Telegram channel."""

    channel_id = int(TELEGRAM_CHANNEL_ID)  # Ensure channel_id is an integer
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID environment variable not set!")
        return []

    last_update_id = redis_client.get('last_telegram_update_id')
    last_update_id = int(last_update_id) if last_update_id else None
    new_posts = []

    try:
        updates = await bot.get_updates(offset=last_update_id + 1 if last_update_id else None, allowed_updates=[telegram.Update.MESSAGE], timeout=60)  # Long polling
        for update in updates:
            # Correctly check for channel posts and captions
            if update.message and update.message.chat.id == channel_id and update.message.caption:
                new_posts.append(update.message)
            # Update after *each* message, to avoid skipping messages on restart.
            redis_client.set('last_telegram_update_id', update.update_id)

    except NetworkError as e:
        logger.error(f"Network error fetching Telegram updates: {e}")
    except RetryAfter as e:
        logger.warning(f"Rate limit exceeded. Retrying after {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)  # Use asyncio.sleep in async context
    except TimedOut as e:
        logger.error(f"Telegram API request timed out: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred fetching Telegram updates: {e}")

    return new_posts

# --- Celery Tasks ---

@celery.task(bind=True, retry_backoff=True, max_retries=5)  # Added max_retries
def update_tv_shows(self):
    """Updates the TV show database with new episodes from Telegram."""
    logger.info("Starting update_tv_shows task...")
    lock = redis_client.lock("update_tv_shows_lock", timeout=600)  # 600-second lock timeout

    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return

    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        posts = asyncio.run(fetch_new_telegram_posts(bot))

        for post in posts:
            post_data = parse_telegram_post(post.caption)

            if post_data:
                logger.info(f"Parsed post data: {post_data}")
                show_name = post_data['show_name']
                season_number = post_data['season']
                episode_number = post_data['episode']
                download_link = post_data['download_link']

                # Use with context manager for database sessions
                show = db.session.query(Show).filter(func.lower(Show.title) == func.lower(show_name)).first()

                if show:
                    logger.info(f"Show '{show_name}' found (ID: {show.id}).")
                    existing_episode = db.session.query(Episodes).filter_by(show_id=show.id, season_number=season_number, episode_number=episode_number).first()

                    if not existing_episode:
                        new_episode = Episodes(title=None, episode_number=episode_number, season_number=season_number, show_id=show.id, download_link=download_link, overview=None)
                        db.session.add(new_episode)
                        # Fetch additional show details only if it's the first episode of the first season AND imdb_id is not already set.
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
                                    show.imdb_id = str(tmdb_id)  # Keep consistent string type
                                    show.available_seasons = show_details.get('number_of_seasons', 1)
                    else:
                        logger.info(f"Episode S{season_number:02d}E{episode_number:02d} of '{show_name}' already exists.")

                else:  # Show not found
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
                                imdb_id=str(tmdb_id),  # Consistent string type
                                download_link=None,  # The show itself has no direct download link
                                available_seasons=show_details.get('number_of_seasons', 1)
                            )
                            db.session.add(new_show)
                            db.session.flush()  # Get the ID for the new show *before* committing

                            #  Add the episode, referencing the new_show.id
                            new_episode = Episodes(
                                title=None,
                                episode_number=episode_number,
                                season_number=season_number,
                                show_id=new_show.id,  # Use the flushed ID
                                download_link=download_link,
                                overview=None,
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
                    self.retry(exc=e, countdown=60)  # Retry after 60 seconds
                except Exception as e:
                    db.session.rollback()
                    logger.exception(f"An error occurred: {e}")
                    self.retry(exc=e, countdown=120)  # Increased retry countdown

    finally:
        lock.release()
        logger.info("update_tv_shows task finished.")
