# tasks.py
from celery import Celery
from celery.exceptions import MaxRetriesExceededError
import os
import re
import requests
# No need to import Bot or Application from telegram.ext
#  because we will use Pyrogram.
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
from redis import Redis
import asyncio  # Import asyncio - CRUCIAL
from datetime import datetime, timezone
from ratelimit import limits, sleep_and_retry, RateLimitException
import difflib  # Import difflib
import json
from pyrogram import Client  # Import Pyrogram Client directly.
from pyrogram.errors import FloodWait, BadRequest
from tv_app.models import db, TVShow  # Absolute import - CORRECT
from tv_app.app import app # Import the app for the application context.


load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Celery configuration (using Redis as the broker and result backend)
celery = Celery(__name__, broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

# --- TMDB Rate Limiting ---
CALLS = 30   # Max calls per period
PERIOD = 9  # Period in seconds

# --- Batch Size ---
TELEGRAM_BATCH_SIZE = 50  # Fetch updates in batches of 50
DATABASE_BATCH_SIZE = 10 # Commit to the database in batches of 10

# --- Redis Client ---
redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True) #For Caching

# --- Helper Functions ---

# NO LONGER ASYNC: This function is now synchronous.
def _fetch_telegram_updates(api_id, api_hash, bot_token, channel_id, limit=TELEGRAM_BATCH_SIZE):
    """Fetches updates from Telegram using Pyrogram, handling offsets correctly."""

    async def _inner_fetch():  # Inner async function to use with asyncio.run()
        try:
            # Use Pyrogram Client directly
            async with Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as pyrogram_client:
                posts = []
                # Use get_chat_history instead of dealing with updates directly
                async for message in pyrogram_client.get_chat_history(chat_id=channel_id, limit=limit):
                    if message.caption:
                        posts.append(message)
                        logger.info(f"Added post to processing list: {message.message_id}")
                return posts
        except FloodWait as e:
            logger.error(f"FloodWait error: {e}.  Waiting for {e.value} seconds.")
            await asyncio.sleep(e.value)  # Wait for the specified time.
            return []  # Return an empty list to avoid breaking the retry logic.
        except BadRequest as e:
            logger.error(f"BadRequest error: {e}")
            return []
        except Exception as e:
            logger.exception(f"Telegram API error in _fetch_telegram_updates: {e}")
            return []

    return asyncio.run(_inner_fetch())  # Run the inner async function.

# NO LONGER ASYNC
def fetch_telegram_posts():
    """Fetches all unacknowledged posts from the configured Telegram channel."""
    logger.info(f"Fetching updates from Telegram channel: {os.environ.get('TELEGRAM_CHANNEL_ID')}")
    # Call _fetch_telegram_updates, passing credentials
    posts = _fetch_telegram_updates(
        int(os.environ.get("API_ID")),  # Pass API_ID as int
        os.environ.get("API_HASH"),     # Pass API_HASH
        os.environ.get("TELEGRAM_BOT_TOKEN"),  # Pass BOT_TOKEN
        os.environ.get("TELEGRAM_CHANNEL_ID")   # Pass CHANNEL_ID
    )
    logger.info(f"Total posts to process: {len(posts)}")
    return posts

def parse_telegram_post(post):
    """Parses a Telegram post caption to extract show info, handling variations."""
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.message_id}, Caption: {text!r}")
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
                'message_id': post.id,  # Use post.id (Pyrogram uses .id)
            }
        else:
            logger.warning(f"No show name found in post: {post.id}")
            return None
    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None
def preprocess_show_name(name):
    """Cleans up the show name before querying TMDb."""
    # Remove common extra text (case-insensitive)
    name = re.sub(r"(?i)\s*(season finale|new episodes|original series|tv series|limited series)\s*", "", name)
    # Remove trailing years (e.g., "Show Name 2023")
    name = re.sub(r"\s*\(\d{4}\)$", "", name)  # (YYYY) at the end
    name = re.sub(r"\s*\d{4}$", "", name)       # YYYY at the end
    # Remove any trailing or leading whitespace
    return name.strip()

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
def call_tmdb_api(url, params):
    """Makes a rate-limited call to the TMDb API."""
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        return response
    except RateLimitException as e:
        logger.warning(f"Rate limit exceeded: {e}")
        raise  # Re-raise to be handled by Celery's retry mechanism
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb API request failed: {e}")
        raise  # Re-raise for Celery retry

def search_tmdb(show_name):
    """Searches TMDb for a TV show, handling rate limits and potential errors."""
    tmdb_api_key = os.environ.get('TMDB_BEARER_TOKEN')
    if not tmdb_api_key:
        logger.error("TMDB_BEARER_TOKEN is not set.")
        return None

    search_url = 'https://api.themoviedb.org/3/search/tv'
    headers = {
        "Authorization": f"Bearer {tmdb_api_key}",
        "accept": "application/json"
    }
    params = {
        'query': preprocess_show_name(show_name),
        'include_adult': 'false',
        'language': 'en-US',
        'page': '1'
    }

    try:
        response = call_tmdb_api(search_url, params=params)
        data = response.json()

        if data.get('results'):
            # Find best match using difflib
            titles = [result['name'] for result in data['results']]
            best_match = difflib.get_close_matches(show_name, titles, n=1, cutoff=0.6)
            if best_match:
                # Find the result that matches the best match
                for result in data['results']:
                    if result['name'] == best_match[0]:
                        return result  # Return the best matching result
            else:
                logger.warning(f"No close match found for '{show_name}' in TMDb results.")
                return None
        else:
            logger.info(f"No results found for '{show_name}' in TMDb.")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error searching TMDb: {e}")
        return None


def get_show_details_tmdb(tmdb_id):
    """Retrieves details for a TV show from TMDb using its ID."""
    tmdb_api_key = os.environ.get('TMDB_BEARER_TOKEN')
    details_url = f'https://api.themoviedb.org/3/tv/{tmdb_id}'
    headers = {
        "Authorization": f"Bearer {tmdb_api_key}",
        "accept": "application/json"
    }
    params = {
        'language': 'en-US',
    }

    try:
        response = call_tmdb_api(details_url, params = params)
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching details from TMDb: {e}")
        return None

# --- Celery Task ---

@celery.task(bind=True, autoretry_for=(RateLimitException, requests.exceptions.RequestException), retry_backoff=True, retry_kwargs={'max_retries': 5})
def update_tv_shows(self):
    """Celery task to fetch Telegram posts, process them, and update the database."""
    logger.info("Lock acquired, starting update_tv_shows task.")

    try:
        posts = fetch_telegram_posts()
        if not posts:
          logger.info("No new posts found.")
          return

        processed_count = 0
        with app.app_context():  # IMPORTANT: Use app context for database access
            for post in posts:
                post_data = parse_telegram_post(post)
                if post_data:
                    message_id = post_data['message_id']

                    # Check if a show with the same message_id already exists
                    existing_show = TVShow.query.filter_by(message_id=message_id).first()
                    if existing_show:
                        logger.info(f"Show with message_id {message_id} already exists, skipping.")
                        continue  # Skip to the next post

                    tmdb_result = search_tmdb(post_data['show_name'])
                    if tmdb_result:
                        tmdb_id = tmdb_result['id']
                        show_details = get_show_details_tmdb(tmdb_id)

                        if show_details:
                          poster_path = show_details.get('poster_path')
                          poster_url = f'https://image.tmdb.org/t/p/w500{poster_path}' if poster_path else None
                          first_air_date = show_details.get('first_air_date')
                          try:
                            if first_air_date:
                                first_air_date = datetime.strptime(first_air_date, '%Y-%m-%d').date()
                          except (ValueError, TypeError):
                            first_air_date = None
                            logger.warning(f"Invalid or missing first_air_date for show_id {tmdb_id}")

                          new_show = TVShow(
                              show_name=post_data['show_name'],
                              season_episode=post_data['season_episode'],
                              download_link=post_data['download_link'],
                              message_id=message_id,
                              tmdb_id=tmdb_id,
                              overview=show_details.get('overview'),
                              poster_url=poster_url,
                              first_air_date=first_air_date,
                              tmdb_rating=show_details.get('vote_average'),
                              popularity=show_details.get('popularity'),
                              created_at=datetime.now(timezone.utc)
                          )

                          db.session.add(new_show)
                          processed_count += 1
                          if processed_count % DATABASE_BATCH_SIZE == 0:
                              db.session.commit()
                              logger.info(f"Committed batch of {DATABASE_BATCH_SIZE} shows to database.")

            db.session.commit()  # Commit any remaining changes
            logger.info(f"Added {processed_count} new shows to the database.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        raise  # Re-raise the exception to allow Celery to retry

    finally:
      logger.info("Lock released.")
