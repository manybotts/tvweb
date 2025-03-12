# tasks.py
import os
import re
import requests
import logging
import asyncio  # Import asyncio
from dotenv import load_dotenv

from celery import Celery, shared_task
from celery.exceptions import MaxRetriesExceededError
from telegram.ext import Application
from telegram.error import TelegramError
from urllib.parse import quote_plus
from pymongo import MongoClient, ASCENDING
from redis import Redis

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Celery configuration
celery = Celery(__name__, broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
                backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

# Database connection
def get_db():
    client = MongoClient(os.environ.get('MONGO_URI'))
    db = client[os.environ.get('DATABASE_NAME', 'tv_shows')]
    try:
        db.command('ping')
        logger.info("Successfully connected to MongoDB from Celery!")
    except Exception as e:
        logger.error(f"Error connecting to MongoDB from Celery: {e}")
        raise
    return db

# --- Helper Functions ---

async def _fetch_telegram_updates(token, channel_id):
    """Asynchronously fetches updates using telegram.ext.Application."""
    try:
        app = Application.builder().token(token).build()
        updates = await app.bot.get_updates(allowed_updates=['channel_post'], timeout=60)
        await app.shutdown()  # Shutdown the application after use
        posts = []
        for update in updates:
            if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == channel_id:
                if update.channel_post.caption:
                    posts.append(update.channel_post)
        return posts
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
        return []

async def fetch_telegram_posts():
    """Fetches all unacknowledged posts from the configured Telegram channel."""
    logger.info(f"Fetching updates from Telegram channel: {os.environ.get('TELEGRAM_CHANNEL_ID')}")
    posts = await _fetch_telegram_updates(os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_CHANNEL_ID'))
    logger.info(f"Total posts to process: {len(posts)}")
    return posts

def parse_telegram_post(post):
    """Parses a Telegram post (caption) to extract show info."""
    try:
        text = post.caption
        logger.info(f"Parsing post: {post.message_id}, Caption: {text!r}")
        lines = text.splitlines()
        show_name = None
        season_episode = None
        download_link = None

        if len(lines) >= 3:
            show_name = lines[0].strip()
            logger.info(f"Show Name: {show_name}")
            if lines[1].strip().startswith('#_'):
                season_episode = None
                link_line_index = 2
                logger.info("Season/Episode: None (starts with #_)")
            else:
                season_episode = lines[1].strip()
                link_line_index = 2
                logger.info(f"Season/Episode: {season_episode}")

            for i in range(link_line_index, len(lines)):
                line_lower = lines[i].lower()
                if "click here" in line_lower:
                    logger.info(f"Found potential link line: {lines[i]}")
                    # Correctly check for caption_entities existence
                    if post.caption_entities:
                        for entity in post.caption_entities:
                            logger.info(f"  Entity: type={entity.type}, offset={entity.offset}, length={entity.length}, url={entity.url}")
                            if entity.type == 'text_link' and (entity.offset >= sum(len(l) + 1 for l in lines[:i]) and entity.offset < sum(len(l) + 1 for l in lines[:i+1])):
                                download_link = entity.url
                                logger.info(f"Download Link Found: {download_link}")
                                break
                        if download_link:
                            break

        if show_name:
            return {
                'show_name': show_name,
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

def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb."""
    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        # Use Authorization header (Best Practice)
        headers = {
            "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",  # Use Bearer token
            "Content-Type": "application/json"
        }
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url, headers=headers)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
            details_response = requests.get(details_url, headers=headers)
            details_response.raise_for_status()
            details_data = details_response.json()

            logger.info(f"TMDb data found for: {show_name}")
            return {
                'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
                'overview': details_data.get('overview'),
                'vote_average': details_data.get('vote_average'),
            }
        else:
            logger.warning(f"No TMDb data found for: {show_name}")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    """Updates the database with new TV show info from Telegram."""
    try:
        redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
        lock = redis_client.lock("update_tv_shows_lock", timeout=60, blocking_timeout=5)

        if lock.acquire(blocking=False):
            logger.info("Lock acquired, starting update_tv_shows task.")
            try:
                # Run the async fetch_telegram_posts using asyncio.run()
                posts = asyncio.run(fetch_telegram_posts())
                if not posts:
                    logger.info("No new posts found.")
                    return

                db = get_db()
                # Prevent unnecessary index creation.
                if "show_name_1" not in db.tv_shows.index_information():
                    db.tv_shows.create_index([("show_name", ASCENDING)], unique=True)

                db.tv_shows.create_index([("message_id", ASCENDING)])

                for post in posts:
                    parsed_data = parse_telegram_post(post)
                    if parsed_data:
                        logger.info(f"Processing show: {parsed_data['show_name']}")
                        tmdb_data = fetch_tmdb_data(parsed_data['show_name'])
                        show_data = {
                            'show_name': parsed_data['show_name'],
                            'season_episode': parsed_data['season_episode'],
                            'download_link': parsed_data['download_link'],
                            'message_id': parsed_data['message_id'],
                            'overview': tmdb_data.get('overview') if tmdb_data else None,
                            'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                            'poster_path': tmdb_data.get('poster_path') if tmdb_data else None,
                        }
                        logger.debug(f"Show data to be saved: {show_data}")

                        try:
                            db.tv_shows.update_one(
                                {'show_name': parsed_data['show_name']},
                                {'$set': show_data},
                                upsert=True
                            )
                            logger.info(f"Successfully updated/inserted: {parsed_data['show_name']}")
                        except Exception as e:
                            logger.error(f"Error updating database for {parsed_data['show_name']}: {e}")
                            # No raise here, continue with other posts

            finally:
                lock.release()  # Correct lock release
                logger.info("Lock released.")
        else:
            logger.warning("Could not acquire lock. Another update_tv_shows task is likely running.")

    except MaxRetriesExceededError:
        logger.error("Max retries exceeded for update_tv_shows task. No further retries.")  # Corrected
    except Exception as exc:
        logger.exception(f"Task failed: {exc}")
        raise self.retry(exc=exc, countdown=60)  # Re-raise for Celery retry

# Simple test task (Keep this for easy testing)
@celery.task
def test_task():
    logger.info("This is a test task!")
    return "Test task completed"
