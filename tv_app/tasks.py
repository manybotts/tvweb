# tv_app/tasks.py
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
def calculate_content_hash(show_name, episode_title, download_link):
    """Calculates a SHA-256 hash of the show content."""
    show_name = show_name or ""
    episode_title = episode_title or ""
    download_link = download_link or ""
    content_string = f"{show_name}-{episode_title}-{download_link}"
    return hashlib.sha256(content_string.encode('utf-8')).hexdigest()

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
    """Parses a Telegram post, prioritizing structured data, using targeted regex,
    ignoring lines starting with '#', and robustly extracting hyperlinks.
    """
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.message_id}, Caption: {text!r}")

        lines = text.splitlines()
        show_name = None
        season_episode = None
        download_link = None

        # --- 1. Attempt Structured Parsing ---
        # Iterate through lines, skipping those starting with '#'
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('#'):
                continue  # Skip comment lines

            if i == 0 and show_name is None:
                show_name = line
                logger.info(f"Initial Show Name: {show_name}")
            elif i == 1 and season_episode is None:
                season_episode = line
                logger.info(f"Initial Season/Episode: {season_episode or 'None (skipped # line)'}")
            # You could add more lines if needed, always checking for '#'

        # --- 2. Link Extraction (Prioritize Entities) ---
        # IMPORTANT:  Check for 'text_link' entities FIRST.
        download_link = next((entity.url for entity in post.caption_entities if entity.type == 'text_link'), None) if post.caption_entities else None
        logger.info(f"Initial Download Link (from entities): {download_link or 'Not Found'}")

        # --- 3. Fallback to Regex (if needed) ---
        normalized_text = normalize_string(text)

        if not season_episode:
            season_episode_match = re.search(r'(?:s|season)\s*(\d+)\s*(?:e|episode)\s*(\d+)|(\d+)[xX](\d+)', normalized_text, re.IGNORECASE)
            if season_episode_match:
                if season_episode_match.group(1) and season_episode_match.group(2):
                    season_episode = f"S{season_episode_match.group(1).zfill(2)}E{season_episode_match.group(2).zfill(2)}"
                elif season_episode_match.group(3) and season_episode_match.group(4):
                    season_episode = f"{season_episode_match.group(3)}x{season_episode_match.group(4).zfill(2)}"
                logger.info(f"Regex found Season/Episode: {season_episode}")

        # Fallback for link (if not found in entities)
        if not download_link:
            # CORRECTED REGEX:  Use finditer and check the line
            for match in re.finditer(r'(https?://\S+)', text, re.MULTILINE):
                line_start = text.rfind('\n', 0, match.start()) + 1
                line_end = text.find('\n', match.end())
                if line_end == -1:
                    line_end = len(text)
                line = text[line_start:line_end].strip()

                if not line.startswith('#'):
                    download_link = match.group(1)
                    logger.info(f"Regex found Download Link: {download_link}")
                    break  # Important: Stop after finding the first valid link


        # --- 4. Validation and Normalization ---
        if show_name:
            normalized_show_name = normalize_string(show_name)
            return {
                'show_name': normalized_show_name,
                'season_episode': season_episode,
                'download_link': download_link,
                'message_id': post.message_id,
            }
        else:
            logger.warning(f"No show name found in post: {post.message_id}")
            return None

    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None

@sleep_and_retry
@limits(calls=TMDB_CALLS_PER_SECOND, period=TMDB_PERIOD)
def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show metadata (including latest season/episode) with caching and fuzzy matching."""
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    cache_key = f"tmdb:{show_name.lower().replace(' ', '_')}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        logger.info(f"Using cached TMDb data for: {show_name}")
        return eval(cached_data)

    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        headers = {
            "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",
            "Content-Type": "application/json"
        }
        # KEY CHANGE: Use 'search/tv' for TV shows specifically
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            logger.info(f"Direct match found for: {show_name}")
        else:
            logger.warning(f"No direct match for: {show_name}.  Attempting fuzzy match.")
            # KEY CHANGE:  Use 'search/tv' here as well
            search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}&page=1"
            search_response = requests.get(search_url, headers=headers, timeout=10)
            search_response.raise_for_status()
            search_data = search_response.json()
            all_results = search_data['results']

            show_titles = [result['name'] for result in all_results]
            best_match, score = process.extractOne(show_name, show_titles)

            if score >= 80:
                for result in all_results:
                    if result['name'] == best_match:
                        show_id = result['id']
                        logger.info(f"Fuzzy match found: {best_match} (score: {score}) for {show_name}")
                        break
            else:
                logger.warning(f"No close match found for: {show_name} (best score: {score})")
                return None

        details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"  # Still use /tv/ for details
        details_response = requests.get(details_url, headers=headers, timeout=10)
        details_response.raise_for_status()
        details_data = details_response.json()

        # --- Get Latest Season/Episode ---
        latest_season_number = details_data['last_episode_to_air']['season_number'] if details_data.get('last_episode_to_air') else None
        latest_episode_number = details_data['last_episode_to_air']['episode_number'] if details_data.get('last_episode_to_air') else None
        latest_season_episode = None

        if latest_season_number is not None and latest_episode_number is not None :
          latest_season_episode = f"S{str(latest_season_number).zfill(2)}E{str(latest_episode_number).zfill(2)}"

        tmdb_info = {
            'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
            'overview': details_data.get('overview'),
            'vote_average': details_data.get('vote_average'),
            'latest_season_episode': latest_season_episode
        }

        redis_client.setex(cache_key, 86400, str(tmdb_info))
        logger.info(f"Cached TMDb data for: {show_name}")
        return tmdb_info

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    """Updates the database, using TMDb for latest season/episode if missing."""
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=60, blocking_timeout=5)

    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return

    try:
        logger.info("Lock acquired, starting update_tv_shows task.")
        posts = asyncio.run(fetch_new_telegram_posts())
        if not posts:
            logger.info("No new posts found.")
            return

        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow

            for post in posts:
                if redis_client.sismember("processed_messages", post.message_id):
                    continue

                parsed_data = parse_telegram_post(post)
                if not parsed_data:
                    continue

                logger.info(f"Processing show: {parsed_data['show_name']}")
                tmdb_data = fetch_tmdb_data(parsed_data['show_name'])

                if not tmdb_data: #if tmdb_data returns None skip
                    continue

                new_content_hash = calculate_content_hash(
                    parsed_data['show_name'],
                    parsed_data['season_episode'],
                    parsed_data['download_link']
                )

                existing_show = TVShow.query.filter_by(show_name=parsed_data['show_name']).first()

                episode_title = parsed_data['season_episode'] or tmdb_data.get('latest_season_episode')

                if existing_show:
                    logger.info(f"Updating existing show: {parsed_data['show_name']}")
                    existing_show.episode_title = episode_title
                    existing_show.download_link = parsed_data['download_link']
                    existing_show.message_id = post.message_id
                    existing_show.overview = tmdb_data.get('overview') if tmdb_data else None
                    existing_show.vote_average = tmdb_data.get('vote_average') if tmdb_data else None
                    existing_show.poster_path = tmdb_data.get('poster_path') if tmdb_data else None
                    existing_show.content_hash = new_content_hash

                    db.session.commit()
                    logger.info(f"Successfully updated: {parsed_data['show_name']}")

                else:
                    logger.info(f"Inserting new show: {parsed_data['show_name']}")
                    show_data = {
                        'show_name': parsed_data['show_name'],
                        'episode_title': episode_title,
                        'download_link': parsed_data['download_link'],
                        'message_id': post.message_id,
                        'overview': tmdb_data.get('overview') if tmdb_data else None,
                        'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                        'poster_path': tmdb_data.get('poster_path') if tmdb_data else None,
                        'content_hash': new_content_hash,
                    }
                    new_show = TVShow(**show_data)
                    db.session.add(new_show)
                    db.session.commit()
                    logger.info(f"Successfully inserted: {parsed_data['show_name']}")

                redis_client.sadd("processed_messages", post.message_id)

            db.session.remove()

    except MaxRetriesExceededError:
        logger.error("Max retries exceeded for update_tv_shows task.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        logger.error(f"Task ID: {self.request.id}")
        self.retry(exc=e, countdown=60)
    finally:
        lock.release()
        logger.info("Lock released.")

@celery.task
def test_task():
    logger.info("The test Celery task has run!")
    return "Test task complete"
