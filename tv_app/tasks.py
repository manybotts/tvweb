from celery import Celery
import os
import re
import requests
import logging
from dotenv import load_dotenv
import asyncio
from pyrogram import Client, errors

load_dotenv()

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

celery = Celery(__name__, broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), backend=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

DATABASE_BATCH_SIZE = 10

api_id = int(os.environ.get("API_ID"))
api_hash = os.environ.get("API_HASH")
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
channel_id = int(os.environ.get('TELEGRAM_CHANNEL_ID'))

async def fetch_telegram_posts():
    logger.info(f"Fetching updates from Telegram channel: {channel_id}")
    posts = []
    try:
        async with Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token) as client:
            async for message in client.get_chat_history(chat_id=channel_id):
                if message.caption:
                    posts.append(message)
                    logger.debug(f"Added post to processing list: {message.id}")  # DEBUG level
    except errors.FloodWait as e:
        logger.warning(f"FloodWait error: {e}. Waiting for {e.value} seconds.")
        await asyncio.sleep(e.value)
    except Exception as e:
        logger.exception(f"Error fetching posts: {e}")
    logger.info(f"Total posts fetched: {len(posts)}")  # INFO level
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
    # Stripped-down version for testing - NO preprocessing, NO caching
    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        headers = {
            "Authorization": f"Bearer {os.environ.get('TMDB_BEARER_TOKEN')}",
            "Content-Type": "application/json"
        }
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}"
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']  # Take the first result
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
            details_response = requests.get(details_url, headers=headers, timeout=10)
            details_response.raise_for_status()
            details_data = details_response.json()

            logger.debug(f"TMDb data found for: {show_name}")  # DEBUG level
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
            posts = asyncio.run(fetch_telegram_posts())
            if not posts:
                logger.info("No new posts found.")
                return

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
                        logger.info(f"Updated show: {parsed_data['show_name']} (ID: {existing_show.id})")  # Include ID
                    else:
                        new_show = TVShow(**show_data)
                        db.session.add(new_show)
                        db.session.commit()
                        logger.info(f"Inserted new show: {parsed_data['show_name']} (ID: {new_show.id})") # Include ID
            db.session.remove()

    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        # No retry in this simplified version
