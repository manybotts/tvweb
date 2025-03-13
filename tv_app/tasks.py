from celery import Celery
import os
import re
import requests
import logging
from dotenv import load_dotenv
import asyncio
from pyrogram import Client, errors
from urllib.parse import quote_plus
from celery.schedules import crontab  # Import crontab

load_dotenv()

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Celery Configuration (Directly in tasks.py) ---
celery = Celery(__name__)
celery.conf.broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery.conf.result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
celery.conf.timezone = 'UTC'  # Set timezone
celery.conf.enable_utc = True
celery.conf.worker_redirect_stdouts = False  # Prevent Celery from overriding logging
celery.conf.worker_hijack_root_logger = False # Prevent celery to hijacking root logger
celery.conf.beat_schedule = {  # Celery Beat schedule
    'update-tv-shows-every-1-minute': {  # Unique name (for testing - change later!)
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute='*/1'), # Every minute (for testing)
        # 'args': (16, 16)  # Optional arguments to the task
    },
}

# --- (Rest of your tasks.py code - from the previous "Suggested Fixes" response) ---

DATABASE_BATCH_SIZE = 10

api_id = int(os.environ.get("API_ID"))
api_hash = os.environ.get("API_HASH")
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
channel_id = int(os.environ.get('TELEGRAM_CHANNEL_ID'))

# --- Pyrogram Client (Global Instance) ---
pyrogram_client = Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

async def fetch_telegram_posts():
    logger.info(f"Fetching updates from Telegram channel: {channel_id}")
    posts = []
    try:
        # Use the global client, but start/stop it correctly.
        await pyrogram_client.start()
        async for message in pyrogram_client.get_chat_history(chat_id=channel_id):
            if message.caption:
                posts.append(message)
                logger.debug(f"Added post to processing list: {message.id}")
    except errors.FloodWait as e:
        logger.warning(f"FloodWait error: {e}. Waiting for {e.value} seconds.")
        await asyncio.sleep(e.value)
        # Retry *after* waiting (important for FloodWait)
        posts.extend(await fetch_telegram_posts())  # Recursive call to retry
    except Exception as e:
        logger.exception(f"Error fetching posts: {e}")
    finally:
        # *Always* stop the client, even if errors occurred.
        await pyrogram_client.stop()
    logger.info(f"Total posts fetched: {len(posts)}")
    return posts

def parse_telegram_post(post):
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.id}, Caption: {text!r}")
        lines = text.splitlines()
        show_name = None
        season_episode = None
        download_link = None

        if len(lines) >= 1:
            show_name = lines[0].strip()
            logger.debug(f"Show Name: {show_name}")

        if len(lines) > 1:
            if lines[1].strip().startswith('#_'):
                season_episode = None
                logger.debug("Season/Episode: None (starts with #_)")
            else:
                season_episode = lines[1].strip()
                logger.debug(f"Season/Episode: {season_episode}")

        # Check if caption_entities exists before iterating
        if post.caption_entities:
            for entity in post.caption_entities:
                if entity.type == "text_link":
                    download_link = entity.url
                    logger.debug(f"Download Link Found: {download_link}")
                    break

        if show_name:
            return {
                'show_name': show_name,
                'season_episode': season_episode,
                'download_link': download_link,
                'message_id': post.id,
            }
        else:
            logger.warning(f"No show name found in post: {post.id}")
            return None

    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None

def fetch_tmdb_data(show_name, language='en-US'):
    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        tmdb_token = os.environ.get('TMDB_BEARER_TOKEN')
        if not tmdb_token:
            logger.error("TMDB_BEARER_TOKEN environment variable not set.")
            return None  # Handle missing token gracefully

        headers = {
            "Authorization": f"Bearer {tmdb_token}",
            "Content-Type": "application/json"
        }
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
            details_response = requests.get(details_url, headers=headers, timeout=10)
            details_response.raise_for_status()
            details_data = details_response.json()

            logger.debug(f"TMDb data found for: {show_name}")
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
        logger.exception(f"An unexpected error occurred fetching TMDb data: {e}")
        return None

@celery.task(bind=True)
def update_tv_shows(self):
    try:
        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            posts = asyncio.run(fetch_telegram_posts())  # Correctly run the async function
            if not posts:
                logger.info("No new posts found.")
                return

            parsed_posts = [parse_telegram_post(post) for post in posts if post]
            if not parsed_posts:
                logger.info("No valid parsed posts.")
                return

            # Efficient duplicate check *before* fetching TMDb data
            message_ids = [post["message_id"] for post in parsed_posts]
            existing_shows = {show.message_id: show for show in TVShow.query.filter(TVShow.message_id.in_(message_ids)).all()}

            for parsed_data in parsed_posts:
                if parsed_data['message_id'] in existing_shows:
                    logger.info(f"Show with message ID {parsed_data['message_id']} already exists. Skipping.")
                    continue  # Skip this post

                logger.info(f"Processing show: {parsed_data['show_name']}")
                tmdb_data = fetch_tmdb_data(parsed_data['show_name'])

                # Handle potential None values from TMDb
                show_data = {
                    'show_name': parsed_data['show_name'],
                    'episode_title': parsed_data['season_episode'],
                    'download_link': parsed_data['download_link'],
                    'message_id': parsed_data['message_id'],
                    'overview': tmdb_data.get('overview') if tmdb_data else None,
                    'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                    'poster_path': tmdb_data.get('poster_path') if tmdb_data else None,
                }

                new_show = TVShow(**show_data)
                db.session.add(new_show)
                logger.info(f"Inserted new show: {parsed_data['show_name']}")

            try:
                db.session.commit()  # Commit *after* processing all posts
                logger.info("All new shows committed to database.")
            except Exception as e:
                db.session.rollback()
                logger.exception("Database commit failed: {e}")
            finally:
                db.session.remove()

    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        # No retry in this simplified version
