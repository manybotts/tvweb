import re
import os
import time
import logging
import requests
import asyncio
from urllib.parse import urlparse, parse_qs, quote_plus
from celery import Celery
from celery.utils.log import get_task_logger
from dotenv import load_dotenv
from models import db, Show, Episode  # Import your models
from ratelimit import limits, sleep_and_retry
from redis import Redis
import telegram
from telegram.error import RetryAfter, TimedOut, NetworkError  # For Telegram errors
from sqlalchemy.exc import OperationalError
from fuzzywuzzy import process

load_dotenv()

# --- Celery Setup ---
celery = Celery(__name__, broker=os.environ.get('REDIS_URL'), backend=os.environ.get('REDIS_URL'))
logger = get_task_logger(__name__)

# --- TMDB API ---
# TMDB_API_KEY = os.environ.get('TMDB_API_KEY') # Not used directly, we use the API_KEYS list
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
API_KEYS = [key for key in API_KEYS if key]  # Remove any None values (if a key isn't set)
current_api_key_index = 0  # Start with the first key

# --- Helper Functions ---
def get_tmdb_data(url, params=None):
    """Fetches data from TMDB, handling API key rotation."""
    global current_api_key_index

    if params is None:
        params = {}

    for attempt in range(len(API_KEYS)):  # Try each key
        params['api_key'] = API_KEYS[current_api_key_index]
        response = requests.get(url, params=params)

        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429 or response.status_code == 401:  # Rate limit or unauthorized
             # 401 is also very important, it will occur when key is invalid.
            logger.warning(f"API Key {API_KEYS[current_api_key_index]} failed (status {response.status_code}). Trying next key...")
            current_api_key_index = (current_api_key_index + 1) % len(API_KEYS)  # Cycle to the next key
        else:
            # Handle other errors (e.g., 500 Internal Server Error)
            logger.error(f"TMDB API error: {response.status_code} - {response.text}")
            return None  # Or raise an exception, depending on how you want to handle errors

    # If all keys fail
    logger.error("All API keys have failed.")
    return None


def get_tmdb_id_by_title(show_title, language='en-US'):
    """Gets the TMDB ID for a show by its title, using caching."""
    cache_key = f"tmdb_id:{show_title}:{language}"
    tmdb_id = redis_client.get(cache_key)

    if tmdb_id is None:
        logger.info(f"TMDB ID for '{show_title}' not found in cache. Fetching from TMDB...")
        search_url = "https://api.themoviedb.org/3/search/tv"
        params = {"query": show_title, "language": language}
        data = get_tmdb_data(search_url, params)  # Use get_tmdb_data for key rotation

        if data and data.get('results'):
            # Direct match (case-insensitive)
            for result in data['results']:
                if result['name'].lower() == show_title.lower():
                    tmdb_id = result['id']
                    logger.info(f"Direct match found: {result['name']} (ID: {tmdb_id})")
                    break
            else:
                 #If no direct match do fuzzy matching
                all_results = data.get('results', [])
                show_titles = [result['name'] for result in all_results]
                best_match, score = process.extractOne(show_name, show_titles) if show_titles else (None, 0)

                if score >= 80:
                    for result in all_results:
                        if result['name'] == best_match:
                            show_id = result['id']
                            logger.info(f"Fuzzy match found: {best_match} (score: {score}) for {show_name}")
                            break  # very important
                else:
                    logger.warning(f"No close match found for: {show_name} (best score: {score})")
                    return None  # <--- THIS IS THE KEY FIX

        else:
            logger.warning(f"No results found in TMDB for show: {show_title}")
            tmdb_id = None

        if tmdb_id:
            redis_client.setex(cache_key, 604800, tmdb_id)  # Cache for 1 week (604800 seconds)

    return tmdb_id


def get_trailer(tmdb_id):
    #  Separate function to get the trailer
    tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/videos"
    data = get_tmdb_data(tmdb_url)  # Use the new function
    trailer_key = None

    if data and data.get('results'):
        for result in data['results']:
            if result['type'] == 'Trailer' and result['site'] == 'YouTube':
                trailer_key = result['key']
                break  # Use the first trailer found

    return trailer_key

def parse_telegram_post(text):
    """
    Parses a Telegram post text to extract show name, season, episode, and download link.
    Returns a dictionary with the extracted data, or None if parsing fails.
    """

    # Improved regex to exclude lines starting with '#'
    match = re.search(r"^(?!#).*S(\d{2})E(\d{2})\s*(.*?)\s*-\s*(https?://\S+)", text, re.MULTILINE | re.IGNORECASE)
    # (?!#) is a negative lookahead that ensures the line doesn't start with #
    # .* match anything after the negative lookahead

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
    else:
        return None

async def fetch_new_telegram_posts(bot):
    """Fetches new posts from the specified Telegram channel."""

    # Use a default value or error handling if the environment variable isn't set
    channel_id = TELEGRAM_CHANNEL_ID
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID environment variable not set!")
        return []

    # Get the last processed update ID from Redis
    last_update_id = redis_client.get('last_telegram_update_id')
    last_update_id = int(last_update_id) if last_update_id else None

    new_posts = []
    try:
        updates = await bot.get_updates(offset=last_update_id + 1 if last_update_id else None, allowed_updates=[telegram.Update.MESSAGE])
        for update in updates:
            if update.message and update.message.chat_id == int(channel_id) and update.message.caption: # Check the caption
                new_posts.append(update.message)
            # Store the *next* update ID to avoid reprocessing
            redis_client.set('last_telegram_update_id', update.update_id)

    except NetworkError as e:
        logger.error(f"Network error fetching Telegram updates: {e}")
    except RetryAfter as e:
        logger.warning(f"Rate limit exceeded. Retrying after {e.retry_after} seconds.")
        time.sleep(e.retry_after)  # Wait before retrying.
    except TimedOut as e:
        logger.error(f"Telegram API request timed out: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred fetching Telegram updates: {e}")

    return new_posts

# --- Celery Tasks ---

@celery.task(bind=True, retry_backoff=True) # Add retry_backoff
def update_tv_shows(self):
    """
    Fetches new posts from Telegram, parses them, and updates the database.
    This task runs periodically.
    """
    logger.info("Starting update_tv_shows task...")

    # Use a Redis lock to prevent concurrent execution of this task
    lock = redis_client.lock("update_tv_shows_lock", timeout=600)  # 600-second lock timeout (10 minutes)

    if lock.acquire(blocking=False):  # Non-blocking acquire
        try:
            bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
            posts =  asyncio.run(fetch_new_telegram_posts(bot))

            for post in posts:
                post_data = parse_telegram_post(post.caption)  # Use caption instead of text

                if post_data:
                    logger.info(f"Parsed post data: {post_data}")
                    show_name = post_data['show_name']
                    season_number = post_data['season']
                    episode_number = post_data['episode']
                    download_link = post_data['download_link']

					# Try to find existing show by name (case-insensitive).  Best practice!
                    show = Show.query.filter(Show.title.ilike(show_name)).first()

                    if show:
                        #Show exists, add a new episode
                        logger.info(f"Show '{show_name}' found in the database (ID: {show.id}).")
                        # Check if the episode already exists
                        existing_episode = Episode.query.filter_by(
                            show_id=show.id,
                            season_number=season_number,
                            episode_number=episode_number
                        ).first()

                        if not existing_episode:
                            new_episode = Episode(
                                title = None, # No episode titles from Telegram data
                                episode_number=episode_number,
                                season_number=season_number,
                                show_id=show.id,
                                download_link=download_link,
                                overview = None,
                            )

                            db.session.add(new_episode)

                            # If we are adding S01E01, and there is no imdb id on the Show,
                            # it means that the Show was created manually (using /admin), not using
                            # the TMDB id route. So now we fill the missing info.

                            if season_number == 1 and episode_number == 1 and not show.imdb_id:
                                # Use the existing caching and rate-limiting
                                tmdb_id = get_tmdb_id_by_title(show.title) # Use helper function
                                if tmdb_id:
                                    tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
                                    show_details = get_tmdb_data(tmdb_url)  # Use the rate-limited function

                                    if show_details:
                                         show.overview = show_details.get('overview')
                                         show.genre = ', '.join([genre['name'] for genre in show_details.get('genres', [])])
                                         show.image_url = f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None
                                         show.trailer_url = f"https://www.youtube.com/watch?v={get_trailer(tmdb_id)}" if get_trailer(tmdb_id) else None
                                         show.imdb_id = str(tmdb_id) #Use TMDB ID
                                         show.available_seasons = show_details.get('number_of_seasons', 1)
                        else:
                            logger.info(f"Episode S{season_number:02d}E{episode_number:02d} of '{show_name}' already exists.")

                    else:
                        # Show doesn't exist, create a new one, fetching data from tmdb
                        logger.info(f"Show '{show_name}' not found.  Fetching from TMDB...")
                        tmdb_id = get_tmdb_id_by_title(show_name) # Use helper function
                        if tmdb_id:
                            tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
                            show_details = get_tmdb_data(tmdb_url) # Use the rate-limited function

                            if show_details:
                                 #  Create and save the show
                                 new_show = Show(
                                     title=show_details.get('name'),
                                     overview=show_details.get('overview'),
                                     release_year=int(show_details.get('first_air_date', '0000-00-00').split('-')[0]) if show_details.get('first_air_date') else None,
                                     genre=', '.join([genre['name'] for genre in show_details.get('genres', [])]),
                                     image_url=f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None,
                                     trailer_url=f"https://www.youtube.com/watch?v={get_trailer(tmdb_id)}" if get_trailer(tmdb_id) else None,
                                     imdb_id=str(tmdb_id),  # Use TMDB ID as a string
                                     download_link=None,  # The show itself has no direct download link
                                     available_seasons = show_details.get('number_of_seasons', 1)
                                 )
                                 db.session.add(new_show)
                                 #  Add the episode
                                 new_episode = Episode(
                                     title = None, # No episode titles from Telegram data
                                     episode_number=episode_number,
                                     season_number=season_number,
                                     show_id=new_show.id, # Use the newly created show's ID
                                     download_link=download_link,
                                     overview = None,
                                 )
                                 db.session.add(new_episode)
                            else:
                                logger.warning(f"Could not retrieve details for show ID {tmdb_id} from TMDB.")
                        else:
                            logger.warning(f"Could not find TMDB ID for show: {show_name}")

                    try:
                        db.session.commit()  # Commit after each post to avoid losing data on error
                        logger.info("Changes committed to the database.")
                    except OperationalError as e:
                        db.session.rollback()
                        logger.exception(f"Database operational error: {e}")
                        self.retry(exc=e, countdown=60)  # Retry after 60 seconds
                    except Exception as e:
                        db.session.rollback() # Rollback in case of any error
                        logger.exception(f"An error occurred: {e}")

        except Exception as e:
            logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
            logger.error(f"Task ID: {self.request.id}")
            self.retry(exc=e, countdown=120)  # Retry after 60 seconds
        finally:
            lock.release()  # Always release the lock
            logger.info("update_tv_shows task finished.")

    else:
        logger.info("update_tv_shows task is already running. Skipping.")
