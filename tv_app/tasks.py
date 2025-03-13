# tasks.py
from celery import Celery
from celery.exceptions import MaxRetriesExceededError
import os
import re
import requests
#from telegram import Bot # No longer needed
#from telegram.error import TelegramError # No longer needed
from telegram.ext import Application
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
from redis import Redis
import asyncio
from datetime import datetime, timezone
#from ratelimit import limits, sleep_and_retry, RateLimitException # Removed for simplicity
#import difflib  # Removed difflib
import json
from pyrogram import Client, filters, errors  # Import Pyrogram


load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Celery configuration (using Redis as the broker and result backend)
celery = Celery(__name__, broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))


# --- TMDB Rate Limiting ---
#CALLS = 30   # Max calls per period  -- REMOVED FOR SIMPLICITY
#PERIOD = 9  # Period in seconds -- REMOVED

# --- Batch Size ---
#TELEGRAM_BATCH_SIZE = 50  # No longer needed
DATABASE_BATCH_SIZE = 10 # Commit to the database in batches of 10

# --- Redis Client ---
redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True) #For Caching

# --- Pyrogram Client Setup ---
# Use the *bot token* as the "api_id".  This is allowed by Pyrogram.
# Use a unique session name.
api_id = int(os.environ.get("API_ID"))
api_hash = os.environ.get("API_HASH")
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
channel_id = int(os.environ.get('TELEGRAM_CHANNEL_ID'))  # Convert to int here

# --- Helper Functions ---

async def _fetch_telegram_updates(token, channel_id):
    """Asynchronously fetches updates, handling offsets."""
    try:
        appli = Application.builder().token(token).build()
        posts = []
        update_offset = None

        while True:  # Loop to fetch all updates
            updates = await appli.bot.get_updates(allowed_updates=['channel_post'], timeout=60, offset=update_offset)
            logger.info(f"Received {len(updates)} updates from Telegram")

            if not updates:
                break

            for update in updates:
                if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == channel_id:
                    if update.channel_post.caption:
                        posts.append(update.channel_post)
                        logger.info(f"Added post to processing list: {update.channel_post.message_id}")
                update_offset = update.update_id + 1
        await appli.shutdown()
        return posts
    except TelegramError as e:
        logger.error(f"Telegram error: {e}")
        return []
    except Exception as e:
        logger.exception(f"Telegram API error: {e}")
        return []

async def fetch_telegram_posts():
   #Fetches all unacknowledged posts from the configured Telegram channel.
    logger.info(f"Fetching updates from Telegram channel: {channel_id}")  # Log the channel ID
    posts = await _fetch_telegram_updates(bot_token, channel_id)
    logger.info(f"Total posts to process: {len(posts)}")
    return posts

def parse_telegram_post(post):
    """Parses a Telegram post caption to extract show info."""
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.id}, Caption: {text!r}")
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
                    logger.debug(f"Found potential link line: {lines[i]}")
                    if post.caption_entities:
                        for entity in post.caption_entities:
                            logger.debug(f"  Entity: type={entity.type}, offset={entity.offset}, length={entity.length}, url={entity.url}")
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
                'message_id': post.id,  # Use post.id
            }
        else:
            logger.warning(f"No show name found in post: {post.id}")
            return None

    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None

# --- NO MORE preprocess_show_name ---
# --- NO MORE get_close_matches_with_threshold ---

# --- TMDB Fetch (Simplified - No Rate Limiting, No Preprocessing) ---
def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb."""
    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        headers = {
            "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",
            "Content-Type": "application/json"
        }
        # Directly use the show_name from Telegram, without preprocessing
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id'] # Simplification: take the first result directly
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
            details_response = requests.get(details_url, headers=headers, timeout=10)
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
        return None  # Don't retry on request exceptions
    except Exception as e:
        logger.exception(f"An unexpected error occurred fetching TMDb data: {e}")
        return None


@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    """Updates the database with new TV show info from Telegram."""
    try:
        redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
        lock = redis_client.lock("update_tv_shows_lock", timeout=120, blocking_timeout=5)

        if lock.acquire(blocking=False):
            logger.info("Lock acquired, starting update_tv_shows task.")
            try:
                posts = fetch_telegram_posts()  # Call the synchronous function directly
                if not posts:
                    logger.info("No new posts found.")
                    return

                from tv_app.app import app
                with app.app_context():
                    from tv_app.models import db, TVShow

                    for post in posts:
                        parsed_data = parse_telegram_post(post)
                        if parsed_data:
                            logger.info(f"Processing show: {parsed_data['show_name']}")
                            tmdb_data = fetch_tmdb_data(parsed_data['show_name']) # Call directly

                            show_data = {
                                'show_name': parsed_data['show_name'],
                                'episode_title': parsed_data['season_episode'],
                                'download_link': parsed_data['download_link'],
                                'message_id': parsed_data['message_id'],
                                'overview': tmdb_data.get('overview') if tmdb_data else None,
                                'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                                'poster_path': tmdb_data.get('poster_path') if tmdb_data else None,
                            }

                            existing_show = TVShow.query.filter_by(message_id=parsed_data['message_id']).first()
                            if existing_show:
                                for key, value in show_data.items():
                                    setattr(existing_show, key, value)
                                db.session.commit()
                                logger.info(f"Successfully updated: {parsed_data['show_name']}")
                            else:
                                new_show = TVShow(**show_data)
                                db.session.add(new_show)
                                db.session.commit()
                                logger.info(f"Successfully inserted: {parsed_data['show_name']}")
                    db.session.remove() #Close db connection
            finally:
                lock.release()
                logger.info("Lock released.")
        else:
            logger.info("Could not acquire lock, task is likely already running.")
    except MaxRetriesExceededError:
        logger.error("Max retries exceeded for update_tv_shows task.")

    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        self.retry(exc=e, countdown=60)  # Retry on other exceptions
