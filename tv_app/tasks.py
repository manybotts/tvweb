# tv_app/tasks.py - DEFINITIVE FINAL version with all logic preserved and enhanced
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
    """Normalizes a string for searching: lowercase, removes emojis/special chars."""
    if text is None: return ""
    text = ''.join(c for c in text if c.isprintable())
    text = text.lower()
    text = re.sub(r'[^\w\s,&\'-.:]', '', text) # Keep colon for titles like "Dead City"
    return re.sub(r'\s+', ' ', text).strip()

def parse_season_info(line: str) -> Optional[int]:
    """Correctly parses a line to find the highest season number from ranges."""
    # Find all individual numbers in the line
    numbers = re.findall(r'\d+', line)
    if not numbers:
        return None

    # Convert all found numbers to integers and return the highest one
    season_numbers = [int(num) for num in numbers]
    return max(season_numbers) if season_numbers else None

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
    """Parses a Telegram post using the robust, multi-stage logic."""
    try:
        text = post.caption
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        if len(lines) < 2: return None

        full_title_from_post = lines[0]
        season_episode_from_post = lines[1]

        normalized_title = normalize_string(full_title_from_post)
        show_name_for_search = re.sub(r'\s+\d{4}$', '', normalized_title).strip()
        search_year_match = re.search(r'(\d{4})$', normalized_title)
        search_year = int(search_year_match.group(1)) if search_year_match else None

        search_season = parse_season_info(season_episode_from_post)

        # --- Flexible, Multi-Method Link Parsing (RESTORED and PRESERVED) ---
        download_link_from_post = None
        if post.caption_entities:
            for entity in post.caption_entities:
                if entity.type == 'text_link':
                    entity_text = text[entity.offset:entity.offset + entity.length]
                    if "click here" in entity_text.lower():
                        download_link_from_post = entity.url
                        break

        if not download_link_from_post and post.caption_entities:
             for entity in reversed(post.caption_entities):
                if entity.type == 'text_link':
                    entity_text = text[entity.offset:entity.offset + entity.length]
                    if '#_' not in entity_text:
                        download_link_from_post = entity.url
                        break

        if not download_link_from_post:
            for line in reversed(lines):
                if '#_' not in line:
                    match = re.search(r'(https?://\S+)', line)
                    if match:
                        download_link_from_post = match.group(1)
                        break

        return {
            'show_name_for_search': show_name_for_search,
            'search_year': search_year,
            'search_season': search_season,
            'season_episode_from_post': season_episode_from_post,
            'download_link_from_post': download_link_from_post,
            'message_id': int(post.message_id),
        }
    except Exception as e:
        logger.exception(f"Error parsing post {post.message_id}: {e}")
        return None

async def fetch_tmdb_data(show_name: str, search_year: Optional[int], search_season: Optional[int]) -> Optional[Dict]:
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

        if not search_data.get('results'): return None

        tmdb_results = search_data['results']

        detailed_results = []
        for result in tmdb_results:
            detail_url = f"{TMDB_BASE_URL}/tv/{result['id']}?language=en-US"
            try:
                async with session.get(detail_url, timeout=5) as resp:
                    if resp.status == 200: detailed_results.append(await resp.json())
            except Exception: continue 

        found_result = None
        if search_year:
            for result in detailed_results:
                if result.get('first_air_date') and str(search_year) in result['first_air_date']:
                    found_result = result
                    break

        if not found_result and search_season:
            best_season_match = None
            smallest_season_diff = float('inf')
            for result in detailed_results:
                season_count = result.get('number_of_seasons', 0)
                if season_count >= search_season:
                    diff = abs(season_count - search_season)
                    if diff < smallest_season_diff:
                        smallest_season_diff = diff
                        best_season_match = result
            if best_season_match: found_result = best_season_match

        if not found_result:
            # --- User-Designed "Fuzz First, Length Last" Logic ---
            show_titles = [r.get('name') for r in detailed_results if r.get('name')]
            if show_titles:
                high_confidence_matches = process.extractBests(show_name, show_titles, score_cutoff=85)

                if high_confidence_matches:
                    best_match_by_length = None
                    smallest_length_diff = float('inf')

                    for match_tuple in high_confidence_matches:
                        match_name = match_tuple[0]
                        diff = abs(len(match_name) - len(show_name))
                        if diff < smallest_length_diff:
                            smallest_length_diff = diff
                            best_match_by_length = match_name

                    if best_match_by_length:
                         for r in detailed_results:
                            if r.get('name') == best_match_by_length:
                                found_result = r
                                break

        if not found_result:
            detailed_results.sort(key=lambda x: x.get('popularity', 0), reverse=True)
            if detailed_results: found_result = detailed_results[0]

        if not found_result: return None

        tmdb_id = found_result.get('id')
        year = int(found_result['first_air_date'][:4]) if found_result.get('first_air_date') else None

        return {
            'tmdb_id': tmdb_id,
            'show_name_from_tmdb': found_result.get('name'),
            'poster_path': f"{TMDB_IMAGE_BASE_URL}{found_result.get('poster_path')}" if found_result.get('poster_path') else None,
            'overview': found_result.get('overview'),
            'vote_average': found_result.get('vote_average'),
            'year': year,
            'rating': found_result.get('vote_average'),
        }

@celery.task(bind=True, retry_backoff=True, max_retries=3)
def update_tv_shows(self):
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=120)
    if not lock.acquire(blocking=False): return

    try:
        posts = asyncio.run(fetch_new_telegram_posts())
        if not posts: return

        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            for post in posts:
                processed_key = f"processed_messages:{post.message_id}"
                if redis_client.exists(processed_key): continue

                parsed_data = parse_telegram_post(post)
                if not parsed_data: continue

                tmdb_data = asyncio.run(fetch_tmdb_data(
                    parsed_data['show_name_for_search'], 
                    parsed_data['search_year'], 
                    parsed_data['search_season']
                ))
                if not tmdb_data: continue

                tmdb_id = tmdb_data.get('tmdb_id')

                existing_show = TVShow.query.filter_by(tmdb_id=tmdb_id).first()
                if existing_show:
                    db.session.delete(existing_show)
                    db.session.commit()

                title_to_save = tmdb_data['show_name_from_tmdb']
                episode_to_save = parsed_data['season_episode_from_post']
                link_to_save = parsed_data['download_link_from_post']

                new_show = TVShow(
                    tmdb_id=tmdb_id,
                    show_name=title_to_save,
                    episode_title=episode_to_save,
                    download_link=link_to_save,
                    message_id=parsed_data['message_id'],
                    poster_path=tmdb_data['poster_path'],
                    overview=tmdb_data['overview'],
                    vote_average=tmdb_data['vote_average'],
                    year=tmdb_data['year'],
                    rating=tmdb_data['rating'],
                    content_hash=f"{tmdb_id}-{episode_to_save}"
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
    except Exception as e:
        logger.exception(f"Error in reset_clicks: {e}")
        db.session.rollback()

@celery.task(name='tv_app.tasks.test_task')
def test_task():
    return "Test task complete"
