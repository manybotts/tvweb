# tasks.py (Part 1)

from celery import Celery
from celery.exceptions import MaxRetriesExceededError
import os
import requests
from telegram import Bot
from telegram.error import TelegramError
from telegram.ext import Application
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
from redis import Redis
import asyncio
from datetime import datetime, timezone
from ratelimit import limits, sleep_and_retry
import hashlib
from thefuzz import fuzz, process
import re
import unicodedata
#Import db and models from tv_app
from tv_app.models import db, Show, Episodes
from tv_app.app import app #Import the app


load_dotenv()

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

celery = Celery(__name__)
celery.config_from_object('celeryconfig')  # Load config!

# TMDb API Rate Limits
TMDB_CALLS_PER_SECOND = 4
TMDB_PERIOD = 1

# --- Helper Functions ---
#Removed calculate hash content

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
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)

    last_offset_key = f"last_telegram_update_id:{channel_id}"
    last_offset = redis_client.get(last_offset_key) or 0
    logger.info(f"Last Telegram Update ID for channel {channel_id}: {last_offset}")

    try:
        appli = Application.builder().token(token).build()
        updates = await appli.bot.get_updates(offset=int(last_offset) + 1, allowed_updates=['channel_post'], timeout=60)
        await appli.shutdown()

        new_posts = []
        for update in updates:
            logger.debug(f"Telegram Update ID: {update.update_id}")
            if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == channel_id:
                if update.channel_post.caption:
                    new_posts.append(update.channel_post)

        if updates:
            redis_client.set(last_offset_key, updates[-1].update_id)

        return new_posts
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
        return []
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return []

def parse_telegram_post(post):
    """
    Parses a Telegram post to extract show information.
    Prioritizes structured data, uses targeted regex,
    ignores lines starting with '#', and robustly extracts hyperlinks.
    """
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.message_id}, Caption: {text!r}")

        lines = text.splitlines()
        show_name = None
        season_number = None
        episode_number = None
        download_link = None

        # --- 1. Attempt Structured Parsing ---
        if len(lines) >= 1:
            show_name = lines[0].strip()
            logger.info(f"Initial Show Name: {show_name}")

            if len(lines) >= 2:
                potential_se = lines[1].strip()
                if not potential_se.startswith("#"):
                    # Attempt to extract S and E numbers directly
                    match = re.search(r'S(\d+)\s?E(\d+)', potential_se, re.IGNORECASE)
                    if match:
                        season_number = int(match.group(1))
                        episode_number = int(match.group(2))
                        logger.info(f"Structured Season/Episode: S{season_number}E{episode_number}")
                    else:
                        logger.info("Structured Season/Episode: Not Found")


        # --- 2. Link Extraction (Prioritize Entities) ---
        download_link = next((entity.url for entity in post.caption_entities if entity.type == 'text_link'), None) if post.caption_entities else None
        logger.info(f"Initial Download Link (from entities): {download_link or 'Not Found'}")


        # --- 3. Fallback to Regex (if needed) ---
        normalized_text = normalize_string(text)

        if season_number is None or episode_number is None:  # Check if we got *both*
            season_episode_match = re.search(r'(?:s|season)\s*(\d+)\s*(?:e|episode)\s*(\d+)|(\d+)[xX](\d+)', normalized_text, re.IGNORECASE)
            if season_episode_match:
                if season_episode_match.group(1) and season_episode_match.group(2):
                    season_number = int(season_episode_match.group(1))
                    episode_number = int(season_episode_match.group(2))
                elif season_episode_match.group(3) and season_episode_match.group(4):
                    season_number = int(season_episode_match.group(3))
                    episode_number = int(season_episode_match.group(4))
                logger.info(f"Regex found Season/Episode: S{season_number}E{episode_number}")

        # Fallback for link (if not found in entities)
        if not download_link:
            url_match = re.search(r'^(?!#)(https?://\S+)', text, re.MULTILINE)
            download_link = url_match.group(1) if url_match else None
            logger.info(f"Regex found Download Link: {download_link or 'Not Found'}")

        # --- 4. Validation and Normalization ---
        if show_name and season_number is not None and episode_number is not None:  # Check all
            normalized_show_name = normalize_string(show_name)
            return {
                'show_name': normalized_show_name,
                'season_number': season_number,
                'episode_number': episode_number,
                'download_link': download_link,
                'message_id': post.message_id,
            }
        else:
            logger.warning(f"Incomplete information found in post: {post.message_id}")
            return None

    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None

@sleep_and_retry
@limits(calls=TMDB_CALLS_PER_SECOND, period=TMDB_PERIOD)
def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show metadata with caching and fuzzy matching, returning necessary data."""
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    cache_key = f"tmdb:{show_name.lower().replace(' ', '_')}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        logger.info(f"Using cached TMDb data for: {show_name}")
        return eval(cached_data)  # Convert string back to dictionary

    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        headers = {
            "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",
            "Content-Type": "application/json"
        }
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        # --- Fuzzy Matching ---
        if search_data['results']:
            show_id = search_data['results'][0]['id']  # Default to first result
            logger.info(f"Direct match found for: {show_name}")
        else:
            # If no direct match, use fuzzy matching
            logger.warning(f"No direct match for: {show_name}. Attempting fuzzy match.")
            show_titles = [result['name'] for result in search_data.get('results', [])]
            best_match, score = process.extractOne(show_name, show_titles)

            if score >= 80:  # Adjust threshold as needed
                for result in search_data['results']:
                    if result['name'] == best_match:
                        show_id = result['id']
                        logger.info(f"Fuzzy match found: {best_match} (score: {score}) for {show_name}")
                        break
            else:
                logger.warning(f"No close match found for: {show_name} (best score: {score})")
                return None

        # --- Fetch Show Details ---
        details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}&append_to_response=seasons"
        details_response = requests.get(details_url, headers=headers, timeout=10)
        details_response.raise_for_status()
        details_data = details_response.json()

        # --- Prepare Data for Caching ---
        tmdb_info = {
            'title': details_data['name'],  # Use the official name
            'overview': details_data.get('overview'),
            'release_year': int(details_data['first_air_date'][:4]) if details_data.get('first_air_date') else None,
            'genre': ', '.join([genre['name'] for genre in details_data.get('genres', [])]),
            'image_url': f"https://image.tmdb.org/t/p/w500{details_data['poster_path']}" if details_data.get('poster_path') else None,
            'trailer_url': None,  # TMDb doesn't provide trailer URLs directly.  You might need another API.
            'imdb_id': details_data.get('external_ids', {}).get('imdb_id'),
            'available_seasons': details_data['number_of_seasons'],
            'clicks': 0,
            'is_new': True,
            "on_slider": False, #Default
        }

        # Cache the TMDb data for 24 hours
        redis_client.setex(cache_key, 86400, str(tmdb_info))  # Store as string
        logger.info(f"Cached TMDb data for: {show_name}")
        return tmdb_info

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None

# ---- END OF PART 1 ----
# tasks.py (Part 2)

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    """Updates the database with new shows and episodes from Telegram."""
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=600, blocking_timeout=5) #Increased timeout to 10min

    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return

    try:
        logger.info("Lock acquired, starting update_tv_shows task.")
        posts = asyncio.run(fetch_new_telegram_posts())
        if not posts:
            logger.info("No new posts found.")
            return

        with app.app_context():
            for post in posts:
                if redis_client.sismember("processed_messages", post.message_id):
                    logger.info(f"Message {post.message_id} already processed. Skipping.")
                    continue

                parsed_data = parse_telegram_post(post)
                if not parsed_data:
                    continue

                show_name = parsed_data['show_name']
                season_number = parsed_data['season_number']
                episode_number = parsed_data['episode_number']
                download_link = parsed_data['download_link']

                logger.info(f"Processing show: {show_name}, S{season_number}E{episode_number}")

                # --- Check if Show Exists ---
                show = Show.query.filter_by(title=show_name).first()

                if not show:
                    # --- Fetch TMDb Data (if show is new) ---
                    tmdb_data = fetch_tmdb_data(show_name)
                    if not tmdb_data:
                        logger.warning(f"Could not fetch TMDb data for {show_name}. Skipping.")
                        continue

                    # --- Create New Show ---
                    logger.info(f"Creating new show: {show_name}")
                    show = Show(**tmdb_data)  # Use the data fetched from TMDb
                    db.session.add(show)
                    db.session.commit()  # Commit to get the show ID
                    #Refresh to avoid detached instance
                    db.session.refresh(show)

                # --- Create or Update Episode ---
                logger.info(f"Checking for episode S{season_number}E{episode_number} of show ID {show.id}")
                episode = Episodes.query.filter_by(show_id=show.id, season_number=season_number, episode_number=episode_number).first()

                if not episode:
                    logger.info(f"Creating new episode for show: {show_name}")
                    episode = Episodes(
                        show_id=show.id,
                        season_number=season_number,
                        episode_number=episode_number,
                        download_link=download_link,
                        title = f"S{season_number}E{episode_number}" #Added default title
                    )
                    db.session.add(episode)
                    logger.info(f"Episode created for show {show.title} : S{season_number}E{episode_number}")
                else:
                    #Update logic (if needed)
                    logger.info(f"Episode S{season_number}E{episode_number} of show ID {show.id} already exists. Updating link")
                    episode.download_link = download_link

                db.session.commit()  # Commit changes for each episode
                redis_client.sadd("processed_messages", post.message_id) # Mark as processed

            # db.session.remove() #Not necessary

    except MaxRetriesExceededError:
        logger.error("Max retries exceeded for update_tv_shows task.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        self.retry(exc=e, countdown=60)
    finally:
        lock.release()
        logger.info("Lock released.")

@celery.task
def test_task():
    logger.info("The test Celery task has run!")
    return "Test task complete"

# ---- END OF PART 2 ----
