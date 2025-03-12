import asyncio
import os
import re
from datetime import datetime, timezone
import httpx
from bs4 import BeautifulSoup
from celery import Celery
from dotenv import load_dotenv
import logging
from redis import Redis

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CORRECT CELERY CONFIGURATION ---
# Use the REDIS_URL environment variable (provided by Railway)
celery = Celery(__name__, broker=os.environ.get('REDIS_URL'), backend=os.environ.get('REDIS_URL'))
celery.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)

@celery.task
def test_task():
    print("This is a test task running!")
    return "Test task completed"


@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    try:
        redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
        lock = redis_client.lock("update_tv_shows_lock", timeout=60, blocking_timeout=5)

        if lock.acquire(blocking=False):
            logger.info("Lock acquired, starting update_tv_shows task.")
            try:
                posts = asyncio.run(fetch_telegram_posts())
                if not posts:
                    logger.info("No new posts found.")
                    return

                # --- KEY CHANGE: Import inside app_context ---
                from app import app  # Import the Flask app instance
                with app.app_context():
                    from models import db, TVShow  # Import db and TVShow

                    for post in posts:
                        message_id = int(post['message_id'])
                        existing_show = TVShow.query.filter_by(message_id=message_id).first()

                        if existing_show:
                            logger.info(f"Show with message_id {message_id} already exists. Skipping.")
                            continue

                        show_name, episode_details, download_link = parse_post_data(post['text'])
                        if show_name and episode_details:
                            tv_show = TVShow(
                                message_id=message_id,
                                show_name=show_name,
                                episode_details=episode_details,
                                download_link=download_link,
                                created_at=datetime.now(timezone.utc)
                            )
                            db.session.add(tv_show)
                            db.session.commit()
                            logger.info(f"Added new TV show: {show_name} - {episode_details}")

            except Exception as e:
                logger.error(f"Error in update_tv_shows: {e}", exc_info=True)
                raise  # Re-raise the exception for Celery to handle retries
            finally:
                lock.release()
                logger.info("Lock released.")
        else:
            logger.info("Could not acquire lock, update_tv_shows task is already running.")

    except Exception as e:
        logger.error(f"Error in update_tv_shows outer block: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)  # Retry after 60 seconds

async def fetch_telegram_posts():
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    tmdb_bearer_token = os.environ.get('TMDB_BEARER_TOKEN')  # Get the bearer token

    if not bot_token or not channel_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID environment variables are not set.")
        return []
    posts = []
    async with httpx.AsyncClient() as client:
        try:
            url = f'https://api.telegram.org/bot{bot_token}/getUpdates?offset=-10'
            logger.info(f"Fetching updates from Telegram: {url}")
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()

            if data['ok'] and data['result']:
                for update in data['result']:
                    if 'channel_post' in update and 'text' in update['channel_post']:
                        post_data = {
                            'message_id': update['channel_post']['message_id'],
                            'text': update['channel_post']['text']
                        }
                        posts.append(post_data)

        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching Telegram updates: {e}")
        except Exception as e:
            logger.error(f"Error fetching Telegram updates: {e}")
        return posts

def parse_post_data(text):
    show_name = episode_details = download_link = None

    # Extract show name
    match = re.match(r'(.+?)(?:S\d+E\d+|$)', text)
    if match:
        show_name = match.group(1).strip()
        show_name = re.sub(r'\(.+\)', '', show_name).strip()  # Remove text within parentheses
        show_name = re.sub(r'\[.+\]', '', show_name).strip()  # Remove text within brackets

    # Extract episode details and download link
    match = re.search(r'(S\d+E\d+.*?)(https?://[^\s]+)', text, re.DOTALL)
    if match:
        episode_details = match.group(1).strip()
        download_link = match.group(2).strip()
    return show_name, episode_details, download_link
