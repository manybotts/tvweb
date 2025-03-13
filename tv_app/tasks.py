from celery import Celery
from celery.schedules import crontab
import os
import re
import requests
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
from redis import Redis
import asyncio
from ratelimit import limits, sleep_and_retry
import json
from pyrogram import Client, errors
from contextlib import asynccontextmanager
from celery.beat.embedded_service import EmbeddedService

load_dotenv()

# Logger Configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Celery Configuration
celery = Celery(
    __name__,
    broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'),
    backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
)

celery.conf.timezone = 'UTC'
celery.conf.enable_utc = True
celery.conf.beat_schedule = {
    'update-tv-shows-every-1-minute': {
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute='*/1'),
    },
}

# Embedded Celery Beat
beat_service = EmbeddedService(celery)  

def start_embedded_beat():
    """Start Celery Beat inside the worker after initialization."""
    logger.info("Starting Celery Beat inside the worker...")
    beat_service.start()

# Start Celery Beat when the worker starts
@celery.on_after_configure.connect
def setup_embedded_beat(sender, **kwargs):
    """Ensures that Beat starts correctly after Celery is configured."""
    start_embedded_beat()

# Rate Limiting Constants
CALLS = 30
PERIOD = 9
DATABASE_BATCH_SIZE = 10

# Redis Client with Error Handling
try:
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")
    redis_client = None

# Telegram API Credentials
api_id = int(os.environ.get("API_ID"))
api_hash = os.environ.get("API_HASH")
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
channel_id = int(os.environ.get('TELEGRAM_CHANNEL_ID'))

# Pyrogram Client Context Manager
@asynccontextmanager
async def get_pyrogram_client():
    client = Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
    await client.start()
    try:
        yield client
    finally:
        await client.stop()

async def fetch_telegram_posts():
    """Fetch new posts from Telegram using Pyrogram with proper async handling."""
    logger.info(f"Fetching updates from Telegram channel: {channel_id}")
    posts = []
    async with get_pyrogram_client() as client:
        try:
            async for message in client.get_chat_history(chat_id=channel_id):
                if message.caption:
                    posts.append(message)
        except errors.FloodWait as e:
            logger.warning(f"FloodWait error: {e}. Waiting {e.value} seconds.")
            await asyncio.sleep(e.value)
            posts.extend(await fetch_telegram_posts())  # Retry
        except Exception as e:
            logger.exception(f"Error fetching posts: {e}")
    logger.info(f"Total posts fetched: {len(posts)}")
    return posts

def parse_telegram_post(post):
    """Parses a Telegram post caption."""
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.id}, Caption: {text!r}")
        lines = text.splitlines()

        if len(lines) < 3:
            logger.warning(f"Skipping post {post.id} due to insufficient lines.")
            return None

        show_name = preprocess_show_name(lines[0].strip())
        season_episode = None if lines[1].strip().startswith('#_') else lines[1].strip()
        download_link = None

        for entity in post.caption_entities or []:
            if entity.type == 'text_link':
                download_link = entity.url
                break

        return {
            'show_name': show_name,
            'season_episode': season_episode,
            'download_link': download_link,
            'message_id': post.id,
        }
    except Exception as e:
        logger.exception(f"Error parsing post: {e}")
        return None

def preprocess_show_name(name):
    """Cleans up the show name."""
    return re.sub(r"(?i)\s*(season finale|new episodes|original series|tv series|limited series|hd|4k|fhd)\s*", "", name).strip()

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb, with rate limiting and caching."""
    cache_key = f"tmdb_data:{show_name.lower()}:{language}"
    cached_data = redis_client.get(cache_key) if redis_client else None
    if cached_data:
        logger.info(f"Cache hit for: {show_name}")
        return json.loads(cached_data)

    headers = {"Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}", "Content-Type": "application/json"}
    search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"

    try:
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        if not search_data['results']:
            logger.warning(f"No TMDb data found for: {show_name}")
            return None

        best_match = search_data['results'][0]
        details_url = f"https://api.themoviedb.org/3/tv/{best_match['id']}?language={language}"
        details_response = requests.get(details_url, headers=headers, timeout=10)
        details_response.raise_for_status()
        details_data = details_response.json()

        result = {
            'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
            'overview': details_data.get('overview'),
            'vote_average': details_data.get('vote_average'),
        }
        if redis_client:
            redis_client.setex(cache_key, 7 * 24 * 60 * 60, json.dumps(result))
        return result
    except requests.exceptions.RequestException as e:
        logger.error(f"TMDb API error: {e}")
        return None

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    """Updates the database with new TV show info from Telegram."""
    lock = redis_client.lock("update_tv_shows_lock", timeout=120, blocking_timeout=5) if redis_client else None

    if lock and not lock.acquire(blocking=False):
        logger.info("Another instance is running, skipping this execution.")
        return

    try:
        posts = asyncio.run(fetch_telegram_posts())
        if not posts:
            return

        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            new_shows = []

            for post in posts:
                parsed_data = parse_telegram_post(post)
                if parsed_data:
                    tmdb_data = fetch_tmdb_data(parsed_data['show_name'])
                    parsed_data.update(tmdb_data or {})

                    new_shows.append(TVShow(**parsed_data))

            db.session.bulk_save_objects(new_shows)
            db.session.commit()
    except Exception as e:
        logger.exception(f"Error in update_tv_shows: {e}")
        self.retry(exc=e, countdown=min(300, (self.request.retries + 1) * 30))
    finally:
        if lock:
            lock.release()
