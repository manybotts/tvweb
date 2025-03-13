from celery import Celery
from celery.exceptions import MaxRetriesExceededError
import os
import re
import requests
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
from redis import Redis
import asyncio
from datetime import datetime, timezone
from ratelimit import limits, sleep_and_retry, RateLimitException
import difflib
import json
from pyrogram import Client
from pyrogram.errors import FloodWait, BadRequest

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

celery = Celery(__name__, broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

CALLS = 30
PERIOD = 9
TELEGRAM_BATCH_SIZE = 50
DATABASE_BATCH_SIZE = 10

redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)

def _fetch_telegram_updates(api_id, api_hash, bot_token, channel_id, limit=TELEGRAM_BATCH_SIZE):
    async def _inner_fetch():
        try:
            async with Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as pyrogram_client:
                posts = []
                async for message in pyrogram_client.get_chat_history(chat_id=channel_id, limit=limit):
                    if message.caption:
                        posts.append(message)
                        logger.info(f"Added post to processing list: {message.message_id}")
                return posts
        except FloodWait as e:
            logger.error(f"FloodWait error: {e}.  Waiting for {e.value} seconds.")
            await asyncio.sleep(e.value)
            return []
        except BadRequest as e:
            logger.error(f"BadRequest error: {e}")
            return []
        except Exception as e:
            logger.exception(f"Telegram API error in _fetch_telegram_updates: {e}")
            return []

    return asyncio.run(_inner_fetch())

def fetch_telegram_posts():
    logger.info(f"Fetching updates from Telegram channel: {os.environ.get('TELEGRAM_CHANNEL_ID')}")
    posts = _fetch_telegram_updates(
        int(os.environ.get("API_ID")),
        os.environ.get("API_HASH"),
        os.environ.get("TELEGRAM_BOT_TOKEN"),
        os.environ.get("TELEGRAM_CHANNEL_ID")
    )
    logger.info(f"Total posts to process: {len(posts)}")
    return posts

def parse_telegram_post(post):
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
                'message_id': post.id,
            }
        else:
            logger.warning(f"No show name found in post: {post.id}")
            return None
    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None

def preprocess_show_name(name):
    name = re.sub(r"(?i)\s*(season finale|new episodes|original series|tv series|limited series)\s*", "", name)
    name = re.sub(r"\s*\(\d{4}\)$", "", name)
    name = re.sub(r"\s*\d{4}$", "", name)
    name = name.replace("&", "and").replace("  ", " ")
    name = re.sub(r'[\(\[].*?[\)\]]', '', name)
    name = re.sub(r"(?i)\s*\b(hd|4k|2k|fhd|s\d+|e\d+)\b", "", name)
    return name.strip()

def get_close_matches_with_threshold(query, possibilities, n=3, cutoff=0.6):
    close_matches = difflib.get_close_matches(query, possibilities, n=n, cutoff=cutoff)
    return close_matches

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
def fetch_tmdb_data(show_name, language='en-US'):
    original_show_name = show_name
    show_name = preprocess_show_name(show_name)
    logger.info(f"Fetching TMDb data for (preprocessed): {show_name}")

    cache_key = f"tmdb_data:{show_name.lower()}:{language}"
    cached_data = redis_client.get(cache_key)
    if cached_data:
        logger.info(f"Cache hit for: {show_name}")
        return json.loads(cached_data)

    headers = {
        "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",
        "Content-Type": "application/json"
    }

    search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
    try:
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            tmdb_titles = [result['name'] for result in search_data['results']]
            best_match = get_close_matches_with_threshold(show_name, tmdb_titles, n=1, cutoff=0.6)

            if best_match:
                best_match_index = tmdb_titles.index(best_match[0])
                show_id = search_data['results'][best_match_index]['id']
            else:
                show_id = search_data['results'][0]['id']

            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
            details_response = requests.get(details_url, headers=headers, timeout=10)
            details_response.raise_for_status()
            details_data = details_response.json()

            data_to_cache = {
                'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
                'overview': details_data.get('overview'),
                'vote_average': details_data.get('vote_average'),
            }
            redis_client.setex(cache_key, 7 * 24 * 60 * 60, json.dumps(data_to_cache))
            logger.info(f"TMDb data found and cached for: {show_name}")
            return data_to_cache

        else:
            logger.info(f"No direct match for: {show_name}, trying shortened versions...")
            name_parts = show_name.split()
            for i in range(len(name_parts) - 1, 0, -1):
                shortened_name = " ".join(name_parts[:i])
                if len(shortened_name) < 3:
                    break
                logger.info(f"Trying shortened name: {shortened_name}")
                shortened_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(shortened_name)}&language={language}"
                shortened_response = requests.get(shortened_url, headers=headers, timeout=10)
                if shortened_response.status_code == 200:
                    shortened_data = shortened_response.json()
                    if shortened_data['results']:
                        tmdb_titles = [result['name'] for result in shortened_data['results']]
                        best_match = get_close_matches_with_threshold(shortened_name, tmdb_titles, n=1, cutoff=0.6)

                        if best_match:
                          best_match_index = tmdb_titles.index(best_match[0])
                          show_id = shortened_data['results'][best_match_index]['id']
                          details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
                          details_response = requests.get(details_url, headers=headers, timeout=10)
                          details_response.raise_for_status()
                          details_data = details_response.json()
                          data_to_cache = {
                                'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
                                'overview': details_data.get('overview'),
                                'vote_average': details_data.get('vote_average'),
                            }
                          redis_client.setex(cache_key, 7 * 24 * 60 * 60, json.dumps(data_to_cache))
                          logger.info(f"TMDb data found (with shortened name) and cached for: {show_name}")
                          return data_to_cache
            logger.warning(f"No TMDb data found for: {original_show_name} (or any shortened versions)")
            return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except RateLimitException as e:
        logger.warning(f"TMDb rate limit hit: {e}")
        raise
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
                posts = fetch_telegram_posts()
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
                            tmdb_data = fetch_tmdb_data(parsed_data['show_name'])

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
                    db.session.remove()

            finally:
                lock.release()
                logger.info("Lock released.")
        else:
            logger.info("Could not acquire lock, task is likely already running.")

    except MaxRetriesExceededError:
        logger.error("Max retries exceeded for update_tv_shows task.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        self.retry(exc=e, countdown=60)
