# tv_app/tasks.py - FINAL version with tmdb_id logic
import os
import re
import json
import asyncio
import logging
import hashlib
import unicodedata
from typing import Dict, Optional, List
from datetime import datetime

import aiohttp
from celery import Celery
from celery.exceptions import MaxRetriesExceededError
from dotenv import load_dotenv
from redis import Redis
from thefuzz import process
from urllib.parse import quote_plus

# Load environment variables
load_dotenv()

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Celery Configuration
celery = Celery(__name__)
celery.config_from_object('celeryconfig')

# Constants
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# --- Helper Functions ---
def normalize_string(text: Optional[str]) -> str:
    if text is None: return ""
    text = text.lower()
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C')
    text = re.sub(r'[^\w\s,&\'-]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

async def fetch_new_telegram_posts() -> list:
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    last_offset_key = f"last_telegram_update_id:{channel_id}"
    last_offset = redis_client.get(last_offset_key) or 0

    from telegram.ext import Application

    try:
        appli = Application.builder().token(token).build()
        updates = await appli.bot.get_updates(offset=int(last_offset) + 1, allowed_updates=['channel_post', 'edited_channel_post'], timeout=60)
        await appli.shutdown()

        new_posts = []
        for update in updates:
            post_obj = update.channel_post or update.edited_channel_post
            if (post_obj and post_obj.sender_chat and str(post_obj.sender_chat.id) == channel_id and post_obj.caption):
                new_posts.append(post_obj)

        if updates:
            redis_client.set(last_offset_key, updates[-1].update_id)
        return new_posts
    except Exception as e:
        logger.exception(f"Error fetching Telegram posts: {e}")
        return []

def parse_telegram_post(post) -> Optional[Dict]:
    try:
        text = post.caption
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        if len(lines) < 2: return None

        full_title = lines[0]
        season_episode = lines[1]
        show_name_for_search = re.sub(r'\s+\d{4}$', '', full_title).strip()
        search_year_match = re.search(r'(\d{4})$', full_title)
        search_year = int(search_year_match.group(1)) if search_year_match else None

        download_link = None
        if post.caption_entities:
            for entity in post.caption_entities:
                if entity.type == 'text_link':
                    entity_text = text[entity.offset:entity.offset + entity.length]
                    if "click here" in entity_text.lower():
                        download_link = entity.url
                        break

        return {
            'full_title': full_title,
            'show_name_for_search': show_name_for_search,
            'search_year': search_year,
            'season_episode': season_episode,
            'download_link': download_link,
            'message_id': int(post.message_id),
        }
    except Exception as e:
        logger.exception(f"Error parsing post {post.message_id}: {e}")
        return None

async def fetch_tmdb_data(show_name: str, search_year: Optional[int]) -> Optional[Dict]:
    tmdb_bearer_token = os.environ.get('TMDB_BEARER_TOKEN')
    headers = {"Authorization": f"Bearer {tmdb_bearer_token}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        search_url = f"{TMDB_BASE_URL}/search/tv?query={quote_plus(show_name)}&language=en-US"
        try:
            async with session.get(search_url, timeout=10) as response:
                response.raise_for_status()
                search_data = await response.json()
        except Exception as e:
            logger.error(f"TMDb API request failed for '{show_name}': {e}")
            return None

        if not search_data.get('results'):
            logger.warning(f"No TMDb results for '{show_name}'.")
            return None

        tmdb_results = search_data['results']
        found_result = None

        # Primary Match: Find by year
        if search_year:
            for result in tmdb_results:
                release_date = result.get('first_air_date', '')
                if release_date and str(search_year) in release_date:
                    found_result = result
                    logger.info(f"Found perfect match by year for '{show_name}': {result['name']}")
                    break

        # Fallback Match: Fuzzy match
        if not found_result:
            show_titles = [r.get('name') for r in tmdb_results if r.get('name')]
            if show_titles:
                best_match, score = process.extractOne(show_name, show_titles)
                if score >= 80:
                    for result in tmdb_results:
                        if result.get('name') == best_match:
                            found_result = result
                            logger.info(f"Found fuzzy match (score {score}) for '{show_name}': {result['name']}")
                            break

        if not found_result:
            logger.warning(f"Could not find a confident match for '{show_name}'.")
            return None

        tmdb_id = found_result.get('id')
        year = int(found_result['first_air_date'][:4]) if found_result.get('first_air_date') else None

        return {
            'tmdb_id': tmdb_id,
            'show_name': found_result.get('name'),
            'poster_path': f"{TMDB_IMAGE_BASE_URL}{found_result.get('poster_path')}" if found_result.get('poster_path') else None,
            'overview': found_result.get('overview'),
            'vote_average': found_result.get('vote_average'),
            'year': year,
            'rating': found_result.get('vote_average'),
        }

@celery.task(bind=True, retry_backoff=True, max_retries=3)
def update_tv_shows(self):
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=60)
    if not lock.acquire(blocking=False):
        logger.info("Lock busy, skipping run.")
        return

    try:
        logger.info("Starting update_tv_shows task.")
        posts = asyncio.run(fetch_new_telegram_posts())
        if not posts:
            logger.info("No new posts found.")
            return

        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            for post in posts:
                processed_key = f"processed_messages:{post.message_id}"
                if redis_client.exists(processed_key):
                    continue

                parsed_data = parse_telegram_post(post)
                if not parsed_data: continue

                tmdb_data = asyncio.run(fetch_tmdb_data(parsed_data['show_name_for_search'], parsed_data['search_year']))
                if not tmdb_data: continue

                tmdb_id = tmdb_data.get('tmdb_id')

                # Delete existing show with the same tmdb_id
                existing_show = TVShow.query.filter_by(tmdb_id=tmdb_id).first()
                if existing_show:
                    logger.info(f"Found existing show for TMDb ID {tmdb_id}. Deleting before update.")
                    db.session.delete(existing_show)
                    db.session.commit()

                # Create new show
                logger.info(f"Inserting new show: {parsed_data['full_title']} (TMDb ID: {tmdb_id})")
                new_show = TVShow(
                    tmdb_id=tmdb_id,
                    show_name=parsed_data['full_title'],
                    episode_title=parsed_data['season_episode'],
                    download_link=parsed_data['download_link'],
                    message_id=parsed_data['message_id'],
                    poster_path=tmdb_data['poster_path'],
                    overview=tmdb_data['overview'],
                    vote_average=tmdb_data['vote_average'],
                    year=tmdb_data['year'],
                    rating=tmdb_data['rating'],
                    content_hash=f"{tmdb_id}-{parsed_data['season_episode']}" # Simpler hash
                )
                db.session.add(new_show)
                redis_client.set(processed_key, 1, ex=86400)

            db.session.commit()
    except Exception as e:
        logger.exception(f"An error occurred in update_tv_shows: {e}")
        db.session.rollback()
    finally:
        if lock.locked(): lock.release()

@celery.task(name='tv_app.tasks.reset_clicks')
def reset_clicks():
    try:
        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            TVShow.query.update({TVShow.clicks: 0})
            db.session.commit()
            logger.info(f"Successfully reset clicks.")
    except Exception as e:
        logger.exception(f"Error in reset_clicks: {e}")
        db.session.rollback()

@celery.task(name='tv_app.tasks.test_task')
def test_task():
    logger.info("The test Celery task has run!")
    return "Test task complete"
    
