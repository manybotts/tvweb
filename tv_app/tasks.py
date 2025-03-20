# tv_app/tasks.py
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
from thefuzz import process, fuzz
from .models import db, Show, Episodes  # Correct relative import
from sqlalchemy import func
import json
import datetime  # Import datetime
import unicodedata
import hashlib
from ratelimit import limits, sleep_and_retry


load_dotenv()

# --- Celery Setup ---
#CRUCIAL: Load configuration from celeryconfig.py
celery = Celery(__name__)
celery.config_from_object('celeryconfig') #Load config
celery.conf.timezone = 'UTC'  # Good practice: Set Celery's timezone.
logger = get_task_logger(__name__)

# --- TMDB API ---
TMDB_CALLS_PER_SECOND = 4  # Consider using this with time.sleep() if needed
TMDB_PERIOD = 1

# --- Redis (for caching and locking) ---
redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
TMDB_BEARER_TOKEN = os.environ.get('TMDB_BEARER_TOKEN')


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
def normalize_string(text):
    """Normalizes a string: lowercase, removes emojis/special chars, extra spaces."""
    if text is None:
        return ""
    text = text.lower()
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C')
    text = re.sub(r'[^\w\s,&\'-]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def fetch_new_telegram_posts():
    """Fetches new Telegram posts using channel-specific offset tracking."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')

    last_offset_key = f"last_telegram_update_id"  # Use a consistent key
    last_offset = redis_client.get(last_offset_key) or 0
    logger.info(f"Last Telegram Update ID for channel {channel_id}: {last_offset}")

    try:
        appli = telegram.ext.Application.builder().token(token).build()
        updates = await appli.bot.get_updates(offset=int(last_offset) + 1, allowed_updates=['channel_post'], timeout=60)
        await appli.shutdown()  # Important: Clean up the Application

        new_posts = []
        for update in updates:
            logger.debug(f"Telegram Update ID: {update.update_id}")
            if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == channel_id:
                if update.channel_post.caption:
                    new_posts.append(update.channel_post)

        if updates:
            redis_client.set(last_offset_key, updates[-1].update_id)

        return new_posts
    except telegram.error.TelegramError as e:
        logger.error(f"Telegram error: {e}")
        return []
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return []



def parse_telegram_post(text: str):
    """
    Parses a Telegram post caption to extract show name, season, episode, and download link.

    Args:
        text: The text content of the post's caption.

    Returns:
        A dictionary containing the parsed data, or None if parsing fails.
    """
    if not text:
        return None

    match = re.search(r'^(.*?)\s+(?:S(\d+)E(\d+)|(\d+)x(\d+))\s+-\s+(https?://.*)$', text, re.IGNORECASE)
    if match:
        show_name = match.group(1).strip() if match.group(1) else None
        # Determine which S/E format was used
        if match.group(2) and match.group(3):
            season = int(match.group(2))
            episode = int(match.group(3))
        elif match.group(4) and match.group(5):
            season = int(match.group(4))
            episode = int(match.group(5))
        else:  # Should never happen, but good to be safe
            return None
        download_link = match.group(6).strip() if match.group(6) else None

        if all([show_name, season, episode, download_link]):
            return {
                'show_name': show_name,
                'season': season,
                'episode': episode,
                'download_link': download_link
            }
    return None


def calculate_content_hash(show_name, season, episode, download_link):
    """Calculates a SHA-256 hash of the show content."""
    content_string = f"{show_name}-{season}-{episode}-{download_link}"
    return hashlib.sha256(content_string.encode('utf-8')).hexdigest()

@sleep_and_retry
@limits(calls=TMDB_CALLS_PER_SECOND, period=TMDB_PERIOD)
def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show metadata with caching and fuzzy matching."""
    cache_key = f"tmdb:{show_name.lower().replace(' ', '_')}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        logger.info(f"Using cached TMDb data for: {show_name}")
        return eval(cached_data)  # Use eval() to convert string to dict

    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        headers = {
            "Authorization": f"Bearer {TMDB_BEARER_TOKEN}",
            "Content-Type": "application/json"
        }
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            logger.info(f"Direct match found for: {show_name}")
        else:
            logger.warning(f"No direct match for: {show_name}.  Attempting fuzzy match.")
            #  Re-fetch results (potentially more pages)
            search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}&page=1"
            search_response = requests.get(search_url, headers=headers, timeout=10)
            search_response.raise_for_status()
            search_data = search_response.json()
            all_results = search_data['results']

            show_titles = [result['name'] for result in all_results]
            best_match, score = process.extractOne(show_name, show_titles)

            if score >= 80:  # Adjust threshold as needed
                for result in all_results:
                    if result['name'] == best_match:
                        show_id = result['id']
                        logger.info(f"Fuzzy match found: {best_match} (score: {score}) for {show_name}")
                        break
            else:
                logger.warning(f"No close match found for: {show_name} (best score: {score})")
                return None

        details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
        details_response = requests.get(details_url, headers=headers, timeout=10)
        details_response.raise_for_status()
        details_data = details_response.json()

        # --- Get Latest Season/Episode ---
        latest_season_number = details_data['last_episode_to_air']['season_number'] if details_data.get('last_episode_to_air') else None
        latest_episode_number = details_data['last_episode_to_air']['episode_number'] if details_data.get('last_episode_to_air') else None

        tmdb_info = {
            'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
            'overview': details_data.get('overview'),
            'vote_average': details_data.get('vote_average'),
            'release_year': details_data.get('first_air_date', '')[:4] if details_data.get('first_air_date') else None, # Extract Year
            'genre': ', '.join([genre['name'] for genre in details_data.get('genres', [])]), # Extract Genres
            'trailer_url': None,  # Placeholder, TMDB doesn't have trailer links easily accessible
            'imdb_id': details_data.get('external_ids', {}).get('imdb_id'), # Get IMDb ID
            'available_seasons': [season['season_number'] for season in details_data.get('seasons', [])], #Get Seasons
            'latest_season': latest_season_number,  # Store separately
            'latest_episode': latest_episode_number, # Store separately
        }

        redis_client.setex(cache_key, 86400, str(tmdb_info))  # Cache for 24 hours
        logger.info(f"Cached TMDb data for: {show_name}")
        return tmdb_info

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None
# --- End of Part 1 ---
# --- Start of Part 2 ---

@celery.task(bind=True, retry_backoff=True, max_retries=5)
def update_tv_shows(self):
    """Fetches new posts from Telegram, parses them, and updates the database."""
    logger.info("Starting update_tv_shows task...")
    lock_key = "update_tv_shows_lock"
    lock = redis_client.lock(lock_key, timeout=720)  # Increased timeout slightly

    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return

    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        # Fetch new posts from Telegram, with error handling
        try:
            posts = asyncio.run(fetch_new_telegram_posts())
            logger.info(f"Fetched {len(posts)} new posts from Telegram.")
        except Exception as e:
            logger.exception(f"Error fetching posts from Telegram: {e}")
            raise  # Re-raise to trigger Celery retry

        # Use the application context for database operations
        from tv_app.app import app  # Import inside the task
        with app.app_context():
            from tv_app.models import db, Show, Episodes  # Import inside app context
            for post in posts:
                # --- Check if message ID has been processed ---
                if redis_client.sismember("processed_messages", post.message_id):
                    logger.info(f"Message ID {post.message_id} already processed. Skipping.")
                    continue
                logger.info(f"Post caption: {post.caption}") # Log caption
                logger.info(f"Caption entities: {post.caption_entities}")  #Log entities

                post_data = parse_telegram_post(post.caption)
                if post_data:
                    logger.info(f"Parsed post data: {post_data}")
                    # --- Calculate Content Hash ---
                    content_hash = calculate_content_hash(
                        post_data['show_name'], post_data['season'], post_data['episode'], post_data['download_link']
                    )

                    # --- Check for Existing Hash ---
                    if redis_client.sismember("processed_hashes", content_hash):
                        logger.info(f"Content hash {content_hash} already processed. Skipping.")
                        continue  # Skip this post

                    # --- Find or create the show ---
                    show = Show.query.filter(func.lower(Show.title) == func.lower(post_data['show_name'])).first()
                    if not show:
                        show = Show(title=post_data['show_name'])
                        db.session.add(show)
                        db.session.flush()  # Get the ID for the new show, VERY IMPORTANT
                        logger.info(f"Created new show: {show.title} (ID: {show.id})")

                    # --- Fetch TMDb data (after show creation/retrieval) ---
                    tmdb_data = fetch_tmdb_data(post_data['show_name'])  # Use show.name
                    if tmdb_data:
                        show.image_url = tmdb_data.get('poster_path')
                        show.overview = tmdb_data.get('overview')
                        show.vote_average = tmdb_data.get('vote_average')
                        show.release_year = tmdb_data.get('release_year') # Get from TMDb data
                        show.genre = tmdb_data.get('genre')  # Get from TMDb data
                        show.trailer_url = tmdb_data.get('trailer_url')  # Get from TMDb data
                        show.imdb_id = tmdb_data.get('imdb_id')  # Get from TMDb data
                        show.available_seasons = tmdb_data.get('available_seasons')  # Get from TMDb
                        #No is_new and on_slider since they are set manually

                    # --- Create or update the episode ---
                    episode = Episodes.query.filter_by(show_id=show.id, season_number=post_data['season'], episode_number=post_data['episode']).first()
                    if episode:
                        # Update existing episode
                        episode.download_link = post_data['download_link']
                        #Check for tmdb_data and set the episode title
                        if tmdb_data and tmdb_data.get('latest_season') == post_data['season'] and tmdb_data.get('latest_episode') == post_data['episode']:
                            episode.title = f"S{str(tmdb_data.get('latest_season')).zfill(2)}E{str(tmdb_data.get('latest_episode')).zfill(2)}"
                        logger.info(f"Updated episode: {show.title} S{post_data['season']}E{post_data['episode']}")
                    else:
                        # Create a new episode
                        title = f"S{post_data['season']:02d}E{post_data['episode']:02d}" #Default title

                        #Check for tmdb_data and set the episode title
                        if tmdb_data and tmdb_data.get('latest_season') == post_data['season'] and tmdb_data.get('latest_episode') == post_data['episode']:
                            title = f"S{str(tmdb_data.get('latest_season')).zfill(2)}E{str(tmdb_data.get('latest_episode')).zfill(2)}"

                        episode = Episodes(
                            show_id=show.id,
                            season_number=post_data['season'],
                            episode_number=post_data['episode'],
                            download_link=post_data['download_link'],
                            title = title,
                            overview = None,
                        )
                        db.session.add(episode)
                        logger.info(f"Created new episode: {show.title} S{post_data['season']}E{post_data['episode']}")

                    # --- Commit changes to the database ---
                    db.session.commit()
                    logger.info(f"Database updated for: {show.title} S{post_data['season']}E{post_data['episode']}")

                    # --- Mark message and hash as processed ---
                    redis_client.sadd("processed_messages", post.message_id)
                    redis_client.sadd("processed_hashes", content_hash)  # Add hash
                else:
                    logger.warning(f"Could not parse post: {post.caption}")
            logger.info("Successfully processed all new Telegram posts.") # Log successful completion

        except OperationalError as e:
            logger.error(f"Database operational error: {e}. Retrying...")
            self.retry(exc=e, countdown=60)
        except (telegram.error.TelegramError, requests.exceptions.RequestException) as e:
            # Catch broader Telegram and Requests errors
            logger.error(f"Network/API error: {e}. Retrying...")
            self.retry(exc=e, countdown=30)  # Shorter retry for network issues

        except Exception as e:
            logger.exception(f"An unexpected error occurred: {e}")
            self.retry(exc=e, countdown=60)  # Retry after a delay
        finally:
            lock.release()
            logger.info("Lock released.")
    else:
        logger.info("Could not acquire lock, task is likely already running.")

@celery.task
def log_current_time():
    """Logs the current time (for testing)."""
    current_time = redis_client.time()  # Use Redis for accurate time
    dt = datetime.datetime.fromtimestamp(current_time[0])  # Convert to datetime
    logger.info(f"Current time according to Celery: {dt.isoformat()}+00:00")
# --- End of Part 2 ---
