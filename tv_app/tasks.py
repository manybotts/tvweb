import re
import os
import time
import logging
import requests
import asyncio
from urllib.parse import quote_plus
from celery import Celery
from celery.utils.log import get_task_logger
from dotenv import load_dotenv
from redis import Redis
import telegram
from telegram.error import RetryAfter, TimedOut, NetworkError
from sqlalchemy.exc import OperationalError
from fuzzywuzzy import process
from .models import db, TVShow
import hashlib
import unicodedata

load_dotenv()

celery = Celery(__name__, broker=os.environ.get('REDIS_URL'), backend=os.environ.get('REDIS_URL'))
logger = get_task_logger(__name__)
celery.conf.task_routes = {
    'tv_app.tasks.update_tv_shows': {'queue': 'updates'},
    'tv_app.tasks.test_task': {'queue': 'default'},
}

TMDB_CALLS_PER_SECOND = 4
TMDB_PERIOD = 1

redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')

API_KEYS = [os.environ.get('API_KEY_1'), os.environ.get('API_KEY_2'), os.environ.get('API_KEY_3')]
API_KEYS = [key for key in API_KEYS if key]
current_api_key_index = 0

def get_tmdb_data(url, params=None):
    global current_api_key_index
    if params is None:
        params = {}
    for _ in range(len(API_KEYS)):
        params['api_key'] = API_KEYS[current_api_key_index]
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json()
        elif response.status_code in (429, 401):
            logger.warning(f"API Key {API_KEYS[current_api_key_index][:4]}... failed. Trying next.")
            current_api_key_index = (current_api_key_index + 1) % len(API_KEYS)
        else:
            logger.error(f"TMDB API error: {response.status_code} - {response.text}")
            return None
    logger.error("All API keys failed.")
    return None

def get_tmdb_id_by_title(show_title, language='en-US'):
    cache_key = f"tmdb_id:{show_title}:{language}"
    tmdb_id = redis_client.get(cache_key)
    if tmdb_id:
        return int(tmdb_id)
    search_url = "https://api.themoviedb.org/3/search/tv"
    params = {"query": show_title, "language": language, "include_adult": "false"}
    data = get_tmdb_data(search_url, params)
    if data and data.get('results'):
        results = data['results']
        for result in results:
            if result['name'].lower() == show_title.lower():
                tmdb_id = result['id']
                redis_client.setex(cache_key, 604800, tmdb_id)
                return tmdb_id
        if results:
            most_popular = max(results, key=lambda x: x.get('popularity', 0))
            best_match, score = process.extractOne(show_title, [result['name'] for result in results])
            if score >= 80 and best_match == most_popular['name']:
                tmdb_id = most_popular['id']
                redis_client.setex(cache_key, 604800, tmdb_id)
                return tmdb_id
    return None

def parse_telegram_post(text):
    match = re.search(r"^(?!#|_#).*S(\d{2})E(\d{2})\s*(.*?)\s*-\s*(https?://\S+)", text, re.MULTILINE | re.IGNORECASE)
    if match:
        return {'show_name': match.group(3).strip(), 'season': int(match.group(1)), 'episode': int(match.group(2)), 'download_link': match.group(4)}
    return None

async def fetch_new_telegram_posts(bot):
    channel_id = TELEGRAM_CHANNEL_ID
    if not channel_id:
        return []
    last_update_id = redis_client.get('last_telegram_update_id')
    last_update_id = int(last_update_id) if last_update_id else None
    try:
        updates = await bot.get_updates(offset=last_update_id + 1 if last_update_id else None, allowed_updates=[telegram.Update.MESSAGE], timeout=60)
        new_posts = [update.message for update in updates if update.message and update.message.chat_id == int(channel_id) and update.message.caption]
        if updates:
           redis_client.set('last_telegram_update_id', updates[-1].update_id)
        return new_posts
    except (NetworkError, RetryAfter, TimedOut, Exception) as e:
        logger.error(f"Telegram error: {e}")
        return []

def calculate_content_hash(show_name, season_number, episode_number, download_link):
    content_string = f"{show_name}-{season_number}-{episode_number}-{download_link}"
    return hashlib.sha256(content_string.encode('utf-8')).hexdigest()

def normalize_string(input_string):
    if input_string is None:
        return ""
    text = input_string.lower()
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C')
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

@celery.task(bind=True, retry_backoff=True, max_retries=5)
def update_tv_shows(self):
    lock_id = "update_tv_shows_lock"
    lock = redis_client.lock(lock_id, timeout=600)
    if not lock.acquire(blocking=False):
        return
    try:
        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        posts = asyncio.run(fetch_new_telegram_posts(bot))
        if not posts:
            return
        from tv_app.app import app
        with app.app_context():
            for post in posts:
                post_data = parse_telegram_post(post.caption)
                if not post_data:
                    continue
                show_name, season_number, episode_number, download_link = post_data['show_name'], post_data['season'], post_data['episode'], post_data['download_link']
                content_hash = calculate_content_hash(show_name, season_number, episode_number, download_link)
                if redis_client.sismember("processed_posts", content_hash):
                    continue
                normalized_show_name = normalize_string(show_name)
                show = TVShow.query.filter(func.lower(TVShow.show_name) == normalized_show_name).first()
                if show:
                    existing_episode = TVShow.query.filter_by(show_id=show.id, season_range=season_number, episode_number=episode_number).first()
                    if not existing_episode:
                        new_episode = TVShow(episode_title=None, episode_number=episode_number, season_range=season_number, show_id=show.id, download_link=download_link, overview=None, content_hash=content_hash)
                        db.session.add(new_episode)
                        if season_number == 1 and episode_number == 1 and not show.content_hash:
                            tmdb_id = get_tmdb_id_by_title(show.show_name)
                            if tmdb_id:
                                tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=en-US"
                                show_details = get_tmdb_data(tmdb_url)
                                if show_details:
                                    show.overview = show_details.get('overview')
                                    show.genre = ', '.join([genre['name'] for genre in show_details.get('genres', [])])
                                    show.poster_path = f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None
                                    show.vote_average = show_details.get('vote_average')
                                    show.content_hash = content_hash
                                    show.year = int(show_details.get('first_air_date', '0000-00-00').split('-')[0]) if show_details.get('first_air_date') else None
                                    show.season_range = show_details.get('number_of_seasons', 1)
                                    db.session.commit()
                    else:
                        existing_episode.download_link = download_link
                        existing_episode.content_hash = content_hash
                        db.session.commit()

                else:
                    tmdb_id = get_tmdb_id_by_title(show_name)
                    if tmdb_id:
                        tmdb_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}?language=en-US"
                        show_details = get_tmdb_data(tmdb_url)
                        if show_details:
                            new_show = TVShow(
                                show_name=show_details.get('name'),
                                overview=show_details.get('overview'),
                                year=int(show_details.get('first_air_date', '0000-00-00').split('-')[0]) if show_details.get('first_air_date') else None,
                                genre=', '.join([genre['name'] for genre in show_details.get('genres', [])]),
                                poster_path=f"https://image.tmdb.org/t/p/w500{show_details.get('poster_path')}" if show_details.get('poster_path') else None,
                                vote_average=show_details.get('vote_average'),
                                content_hash=content_hash,
                                download_link=None,
                                season_range=show_details.get('number_of_seasons', 1)
                            )
                            db.session.add(new_show)
                            db.session.commit()
                            new_episode = TVShow(episode_title=None, episode_number=episode_number, season_range=season_number, show_id=new_show.id, download_link=download_link, overview=None, content_hash=content_hash)
                            db.session.add(new_episode)
                            db.session.commit()
                redis_client.sadd("processed_posts", content_hash)
                try:
                    db.session.commit()
                except OperationalError as e:
                    db.session.rollback()
                    self.retry(exc=e, countdown=60)
                except Exception as e:
                    db.session.rollback()
                    self.retry(exc=e, countdown=60)
    except Exception as e:
        self.retry(exc=e, countdown=120)
    finally:
        lock.release()

@celery.task
def test_task():
  return "Test task complete"
