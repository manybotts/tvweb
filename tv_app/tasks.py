# tv_app/tasks.py (Part 1 of 2) - Populating year, rating, and genres
# --- Start of Part 1 ---
from celery import Celery
from celery.exceptions import MaxRetriesExceededError, Retry
from dotenv import load_dotenv
from redis import Redis, exceptions as redis_exceptions
from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackContext
from thefuzz import fuzz, process
from urllib.parse import quote_plus
from ratelimit import limits, sleep_and_retry
import os
import re
import json
import asyncio
import logging
import hashlib
import unicodedata
from typing import Dict, Optional, Tuple, List

import aiohttp

load_dotenv()

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Celery Configuration ---
celery = Celery(__name__)
celery.config_from_object('celeryconfig')

# --- Constants ---
TMDB_CALLS_PER_SECOND = 4
TMDB_PERIOD = 1
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
PROCESSED_MESSAGES_TTL = 86400  # 24 hours in seconds

# --- Helper Functions ---
def calculate_content_hash(show_name: str, episode_title: Optional[str], download_link: Optional[str]) -> str:
    """Calculates a SHA-256 hash of the show content."""
    show_name = show_name or ""
    episode_title = episode_title or ""
    download_link = download_link or ""
    content_string = f"{show_name}-{episode_title}-{download_link}"
    return hashlib.sha256(content_string.encode('utf-8')).hexdigest()

def normalize_string(text: Optional[str]) -> str:
    """Normalizes a string: lowercase, removes emojis/special chars, extra spaces."""
    if text is None:
        return ""
    text = text.lower()
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C')
    text = re.sub(r'[^\w\s,&\'-]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_season_episode(text: str) -> Optional[str]:
    """Extracts season and episode information from a string."""
    match = re.search(r'(?:s|season)\s*(\d+)\s*(?:e|episode)\s*(\d+)|(\d+)[xX](\d+)', text, re.IGNORECASE)
    if match:
        if match.group(1) and match.group(2):
            return f"S{match.group(1).zfill(2)}E{match.group(2).zfill(2)}"
        elif match.group(3) and match.group(4):
            return f"{match.group(3)}x{match.group(4).zfill(2)}"
    return None

async def fetch_new_telegram_posts() -> List[Update]:
    """Fetches new Telegram posts, including edited posts."""
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)

    last_offset_key = f"last_telegram_update_id:{channel_id}"
    last_offset = redis_client.get(last_offset_key) or 0
    logger.info(f"Last Telegram Update ID for channel {channel_id}: {last_offset}")

    try:
        appli = Application.builder().token(token).build()
        updates = await appli.bot.get_updates(offset=int(last_offset) + 1, allowed_updates=['channel_post', 'edited_channel_post'], timeout=60)
        await appli.shutdown()

        new_posts: List[Update] = []
        for update in updates:
            logger.debug(f"Telegram Update ID: {update.update_id}")
            if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == channel_id:
                if update.channel_post.caption:
                    new_posts.append(update.channel_post)
            elif update.edited_channel_post and update.edited_channel_post.sender_chat and str(update.edited_channel_post.sender_chat.id) == channel_id:
                if update.edited_channel_post.caption:
                    new_posts.append(update.edited_channel_post)

        if updates:
            redis_client.set(last_offset_key, updates[-1].update_id)

        return new_posts
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
        return []
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return []

def parse_telegram_post(post: Update) -> Optional[Dict]:
    """Parses a Telegram post, prioritizing structured data and links towards the bottom."""
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.message_id}, Caption: {text!r}")

        lines = text.splitlines()
        show_name = None
        season_episode = None
        download_link = None

        # --- 1. Attempt Structured Parsing ---
        for i, line in enumerate(lines):
            line = line.strip()
            if line.startswith('#') or '#_' in line:
                continue

            if i == 0 and show_name is None:
                show_name = line
                logger.info(f"Initial Show Name: {show_name}")
            elif i == 1 and season_episode is None:
                season_episode = line
                logger.info(f"Initial Season/Episode: {season_episode or 'None'}")

        # --- 2. Link Extraction (Prioritize Entities, Bottom-Up) ---
        if post.caption_entities:
            for entity in reversed(post.caption_entities):
                if entity.type == 'text_link':
                    entity_text = text[entity.offset:entity.offset + entity.length]
                    if '#_' not in entity_text:
                        download_link = entity.url
                        logger.info(f"Entity found Download Link: {download_link}")
                        break

        # --- 3. Fallback to Regex (if needed, Bottom-Up) ---
        normalized_text = normalize_string(text)

        if not season_episode:
            season_episode = parse_season_episode(normalized_text)
            if season_episode:
                logger.info(f"Regex found Season/Episode: {season_episode}")

        if not download_link:
            for line in reversed(lines):
                line = line.strip()
                if '#_' not in line:
                    match = re.search(r'(https?://\S+)', line)
                    if match:
                        download_link = match.group(1)
                        logger.info(f"Regex found Download Link: {download_link}")
                        break

        # --- 4. Validation and Normalization ---
        if show_name:
            normalized_show_name = normalize_string(show_name)
            return {
                'show_name': normalized_show_name,
                'season_episode': season_episode,
                'download_link': download_link,
                'message_id': int(post.message_id),  # Cast to integer
            }
        else:
            logger.warning(f"No show name found in post: {post.message_id}")
            return None

    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None
# --- End of Part 1 ---
# tv_app/tasks.py (Part 2 of 2) - Populating year, rating, and genres
# --- Start of Part 2 ---

@sleep_and_retry
@limits(calls=TMDB_CALLS_PER_SECOND, period=TMDB_PERIOD)
async def fetch_tmdb_data(show_name: str, language: str = 'en-US') -> Optional[Dict]:
    """Fetches TV show metadata asynchronously, with caching and fuzzy matching."""
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    cache_key = f"tmdb:{show_name.lower().replace(' ', '_')}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        logger.info(f"Using cached TMDb data for: {show_name}")
        try:
            return json.loads(cached_data)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in cache for key: {cache_key}.  Fetching fresh data.")
            pass


    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        headers = {
            "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",
            "Content-Type": "application/json"
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            search_url = f"{TMDB_BASE_URL}/search/tv?query={quote_plus(show_name)}&language={language}"
            async with session.get(search_url, timeout=10) as response:
                response.raise_for_status()
                search_data = await response.json()

            if search_data['results']:
                show_id = search_data['results'][0]['id']
                logger.info(f"Direct match found for: {show_name}")
            else:
                logger.warning(f"No direct match for: {show_name}. Attempting fuzzy match.")
                search_url = f"{TMDB_BASE_URL}/search/tv?query={quote_plus(show_name)}&language={language}&page=1"
                async with session.get(search_url, timeout=10) as response:
                    response.raise_for_status()
                    search_data = await response.json()
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

            details_url = f"{TMDB_BASE_URL}/tv/{show_id}?language={language}"
            async with session.get(details_url, timeout=10) as response:
                response.raise_for_status()
                details_data = await response.json()

            latest_season_number = details_data['last_episode_to_air']['season_number'] if details_data.get('last_episode_to_air') else None
            latest_episode_number = details_data['last_episode_to_air']['episode_number'] if details_data.get('last_episode_to_air') else None
            latest_season_episode = f"S{str(latest_season_number).zfill(2)}E{str(latest_episode_number).zfill(2)}" if latest_season_number is not None and latest_episode_number is not None else None

            # --- Extract Year and Rating ---
            year = None
            if details_data.get('first_air_date'):
                try:
                    year = int(details_data['first_air_date'][:4])  # Extract year from date string
                except (ValueError, TypeError):
                    logger.warning(f"Invalid year format for show: {show_name}")

            rating = details_data.get('vote_average')

            # --- Extract Genres ---
            genres_list = [genre['name'] for genre in details_data.get('genres', [])]


            tmdb_info = {
                'poster_path': f"{TMDB_IMAGE_BASE_URL}{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
                'overview': details_data.get('overview'),
                'vote_average': details_data.get('vote_average'),
                'latest_season_episode': latest_season_episode,
                'year': year,  # Add year
                'rating': rating,  # Add rating
                'genres': genres_list  # Add genres
            }

            redis_client.setex(cache_key, 86400, json.dumps(tmdb_info))
            logger.info(f"Cached TMDb data for: {show_name}")
            return tmdb_info

    except aiohttp.ClientError as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None

@celery.task(bind=True, retry_backoff=True, max_retries=3)
def update_tv_shows(self):
    """Updates the database, using TMDb for latest season/episode if missing."""
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    lock_name = "update_tv_shows_lock"
    lock = redis_client.lock(lock_name, timeout=60, blocking_timeout=5)

    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return

    try:
        logger.info("Lock acquired, starting update_tv_shows task.")
        posts: List[Update] = asyncio.run(fetch_new_telegram_posts())
        if not posts:
            logger.info("No new posts found.")
            return

        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow, Genre  # Import Genre

            for post in posts:
                processed_key = "processed_messages:" + str(post.message_id)
                if redis_client.exists(processed_key):
                    logger.info(f"Message {post.message_id} already processed, skipping.")
                    continue

                existing_message = TVShow.query.filter_by(message_id=int(post.message_id)).first()
                if existing_message:
                    logger.info("Edited message, deleting it first")
                    db.session.delete(existing_message)
                    # Don't commit here; commit after all shows

                parsed_data: Optional[Dict] = parse_telegram_post(post)
                if not parsed_data:
                    continue

                logger.info(f"Processing show: {parsed_data['show_name']}")
                tmdb_data: Optional[Dict] = asyncio.run(fetch_tmdb_data(parsed_data['show_name']))

                if not tmdb_data:
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
                    existing_show.message_id = int(post.message_id)
                    existing_show.overview = tmdb_data.get('overview')
                    existing_show.vote_average = tmdb_data.get('vote_average')
                    existing_show.poster_path = tmdb_data.get('poster_path')
                    existing_show.content_hash = new_content_hash

                    # --- Update Year and Rating ---
                    existing_show.year = tmdb_data.get('year')
                    existing_show.rating = tmdb_data.get('rating')

                    # --- Update Genres (Many-to-Many) ---
                    existing_genres = {genre.name for genre in existing_show.genres}
                    new_genres = set(tmdb_data.get('genres', []))

                    # Add new genres
                    for genre_name in new_genres - existing_genres:
                        genre = Genre.query.filter_by(name=genre_name).first()
                        if not genre:
                            genre = Genre(name=genre_name)
                            db.session.add(genre)  # Add to session if it's new
                        existing_show.genres.append(genre)

                    # Remove old genres (that are no longer present)
                    for genre in existing_show.genres:
                        if genre.name not in new_genres:
                            existing_show.genres.remove(genre)


                    # Don't commit here
                    # db.session.commit()
                    logger.info(f"Successfully updated: {parsed_data['show_name']}")

                else:
                    logger.info(f"Inserting new show: {parsed_data['show_name']}")
                    show_data = {
                        'show_name': parsed_data['show_name'],
                        'episode_title': episode_title,
                        'download_link': parsed_data['download_link'],
                        'message_id': int(post.message_id),
                        'overview': tmdb_data.get('overview'),
                        'vote_average': tmdb_data.get('vote_average'),
                        'poster_path': tmdb_data.get('poster_path'),
                        'content_hash': new_content_hash,
                        'year': tmdb_data.get('year'),  # Add year
                        'rating': tmdb_data.get('rating')  # Add rating
                    }
                    new_show = TVShow(**show_data)

                    # --- Add Genres (Many-to-Many) ---
                    for genre_name in tmdb_data.get('genres', []):
                        genre = Genre.query.filter_by(name=genre_name).first()
                        if not genre:
                            genre = Genre(name=genre_name)
                            db.session.add(genre)  # Add to session if new
                        new_show.genres.append(genre)  # Associate genre with show

                    db.session.add(new_show)
                    # Don't commit here
                    #db.session.commit()
                    logger.info(f"Successfully inserted: {parsed_data['show_name']}")

                redis_client.set(processed_key, 1, ex=PROCESSED_MESSAGES_TTL)
            db.session.commit() #Commit at once
            db.session.remove()

    except redis_exceptions.ConnectionError as e:
        logger.error(f"Redis connection error: {e}")
        self.retry(exc=e, countdown=60)
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

# --- End of Part 2 ---
