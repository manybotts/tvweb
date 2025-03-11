# tasks.py
from celery import Celery
import os
import re
import requests
from telegram import Bot
from telegram.error import TelegramError
from urllib.parse import quote_plus
from pymongo import MongoClient, ASCENDING, DESCENDING
import logging
from dotenv import load_dotenv
import asyncio

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Celery configuration (using Redis as the broker and result backend)
celery = Celery(__name__, broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))
# Use REDIS_URL environment variable - Railway provides this

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

async def fetch_telegram_posts():
    """Fetches all unacknowledged posts from the configured Telegram channel."""
    try:
        bot = Bot(token=os.environ.get('TELEGRAM_BOT_TOKEN'))
        logger.info(f"Fetching updates from Telegram channel: {os.environ.get('TELEGRAM_CHANNEL_ID')}")

        posts = []
        update_offset = None  # Initialize the offset

        while True:  # Loop to retrieve all updates
            updates = await bot.get_updates(allowed_updates=['channel_post'], timeout=60, offset=update_offset)
            logger.info(f"Received {len(updates)} updates from Telegram")

            if not updates:  # No more updates
                break

            for update in updates:
                if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == os.environ.get('TELEGRAM_CHANNEL_ID'):
                    if update.channel_post.caption:
                        posts.append(update.channel_post)
                        logger.info(f"Added post to processing list: {update.channel_post.message_id}")  # Log added posts

                # Update the offset to the *next* update ID
                update_offset = update.update_id + 1

        logger.info(f"Total posts to process: {len(posts)}") #Log total posts
        return posts

    except TelegramError as e:
        logger.error(f"Error fetching updates from Telegram: {e}")
        return []
    except Exception as e:
        logger.exception(f"An unexpected error occurred in fetch_telegram_posts: {e}")
        return []

def parse_telegram_post(post):
    """Parses a Telegram post (caption) to extract show info."""
    try:
        text = post.caption
        logger.info(f"Parsing post: {post.message_id}, Caption: {text!r}") # Log the entire caption
        lines = text.splitlines()
        logger.info(f"Lines: {lines}") # Log the lines
        show_name = None
        season_episode = None
        download_link = None

        if len(lines) >= 3 : #we need at least 3 lines
            show_name = lines[0].strip()
            logger.info(f"Show Name: {show_name}") # Log show name
            # Check if second line is valid or starts with '#_'
            if lines[1].strip().startswith('#_'):
                season_episode = None  # No season/episode info
                link_line_index = 2 # Check from third line
                logger.info("Season/Episode: None (starts with #_)") # Log season/episode status
            else:
                season_episode = lines[1].strip()
                link_line_index = 2
                logger.info(f"Season/Episode: {season_episode}") # Log season/episode

            #Find the Donwload link
            for i in range(link_line_index, len(lines)):
                line_lower = lines[i].lower()

                if "click here" in line_lower: #Changed
                    logger.info(f"Found potential link line: {lines[i]}") # Log potential link line
                    if post.caption_entities:
                        for entity in post.caption_entities:
                            logger.info(f"  Entity: type={entity.type}, offset={entity.offset}, length={entity.length}, url={entity.url}") # Log each entity
                            if entity.type == 'text_link' and (entity.offset >= sum(len(l) + 1 for l in lines[:i]) and entity.offset < sum(len(l) + 1 for l in lines[:i+1])):
                                download_link = entity.url
                                logger.info(f"Download Link Found: {download_link}") # Log found link
                                break  # Stop after finding the first link
                    if download_link:
                        break

        if show_name:  # Only return data if show_name was found
            return {
                'show_name': show_name,
                'season_episode': season_episode,
                'download_link': download_link,
                'message_id': post.message_id,
            }
        else:
            logger.warning(f"No show name found in post: {post.message_id}") # Log if no show name
            return None
    except Exception as e:
      logger.error(f"Error during parsing: {e}")
      return None

def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb."""
    try:
        logger.info(f"Fetching TMDb data for: {show_name}")  # Log TMDb fetch attempt
        search_url = f"https://api.themoviedb.org/3/search/tv?api_key={os.environ.get('TMDB_API_KEY')}&query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?api_key={os.environ.get('TMDB_API_KEY')}&language={language}"
            details_response = requests.get(details_url)
            details_response.raise_for_status()
            details_data = details_response.json()

            logger.info(f"TMDb data found for: {show_name}")  # Log success

            return {
                'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
                'overview': details_data.get('overview'),
                'vote_average': details_data.get('vote_average'),
            }
        else:
            logger.warning(f"No TMDb data found for: {show_name}") # Log if no data found
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    try:
        async def async_helper():
            posts = await fetch_telegram_posts()
            if not posts:
                logger.info("No new posts found.")
                return

            db = get_db()
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
                    logger.debug(f"Show data to be saved: {show_data}") # Log the data before saving

                    # TEMPORARY: Bypass the database update for debugging
                    # try:
                    #     db.tv_shows.update_one(
                    #         {'show_name': parsed_data['show_name']},
                    #         {'$set': show_data},
                    #         upsert=True
                    #     )
                    #     logger.info(f"Successfully updated/inserted: {parsed_data['show_name']}")
                    # except Exception as e:
                    #     logger.error(f"Error updating database for {parsed_data['show_name']}: {e}")
                    #     raise  # Re-raise for Celery retry
                    logger.info(f"TEMPORARY: Skipping database update for {parsed_data['show_name']}") #Temporary

            db.tv_shows.create_index([("show_name", ASCENDING)], unique=True)
            db.tv_shows.create_index([("message_id", ASCENDING)])
        asyncio.run(async_helper())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)
