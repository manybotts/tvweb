import os
import re
import requests
from celery import Celery
from celery.exceptions import MaxRetriesExceededError
from telethon import TelegramClient, events, types
from telethon.sessions import StringSession
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
from redis import Redis
from thefuzz import process
from flask import Flask

# --- Imports for Flask and Database ---
from tv_app.models import db, TVShow  # Import your models

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Telethon Setup (Bot Account) ---
API_ID = int(os.environ.get('TELEGRAM_API_ID'))
API_HASH = os.environ.get('TELEGRAM_API_HASH')
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = int(os.environ.get('TELEGRAM_CHANNEL_ID'))

# --- Helper Functions ---

def parse_telegram_post(post):
    """Parses a Telegram post."""
    try:
        text = post.message.text
        logger.debug(f"Parsing post: {post.message.id}, Caption: {text!r}")
        lines = text.splitlines()
        show_name, season_episode, download_link = None, None, None

        if len(lines) >= 3:
            show_name = lines[0].strip()
            logger.info(f"Show Name: {show_name}")
            season_episode = None if lines[1].strip().startswith('#_') else lines[1].strip()
            link_line_index = 2 if season_episode is None else 2
            logger.info(f"Season/Episode: {season_episode or 'None (starts with #_)'}")

            for i in range(link_line_index, len(lines)):
                if "click here" in lines[i].lower():
                    logger.debug(f"Found potential link line: {lines[i]}")
                    if post.message.entities:
                        for entity in post.message.entities:
                            logger.debug(f"  Entity: type={type(entity).__name__}, offset={entity.offset}, length={entity.length}")
                            if isinstance(entity, types.MessageEntityTextUrl):
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
                'message_id': post.message.id,
            }
        else:
            logger.warning(f"No show name found in post: {post.message.id}")
            return None
    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None
def clean_show_name(show_name):
    return ''.join(e for e in show_name if e.isalnum() or e.isspace()).strip().lower()

def search_tmdb(api_key, query):
    url = f"https://api.themoviedb.org/3/search/tv?api_key={api_key}&query={quote_plus(query)}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json().get('results', [])

def find_best_match(show_name, tmdb_results):
    tmdb_titles = [result['name'] for result in tmdb_results]
    best_match, score = process.extractOne(show_name, tmdb_titles)
    return best_match if score > 80 else None

def get_series_id(api_key, show_name):
    cleaned_name = clean_show_name(show_name)
    results = search_tmdb(api_key, cleaned_name)
    if results:
        best_match = find_best_match(cleaned_name, results)
        if best_match:
            for result in results:
                if result['name'] == best_match:
                    return result['id']
    return None

def get_season_count(api_key, series_id):
    details_url = f"https://api.themoviedb.org/3/tv/{series_id}?api_key={api_key}"
    response = requests.get(details_url, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data.get('number_of_seasons', 0)

def get_latest_episode(api_key, series_id, season_number):
    season_url = f"https://api.themoviedb.org/3/tv/{series_id}/season/{season_number}?api_key={api_key}"
    response = requests.get(season_url, timeout=10)
    response.raise_for_status()
    data = response.json()
    episodes = data.get('episodes', [])
    if episodes:
        latest_episode = episodes[-1]
        return latest_episode['name'], latest_episode['episode_number']
    return None, None

def fetch_tmdb_data(show_name, language='en-US'):
    try:
        logger.info(f"Fetching TMDb data for: {show_name}")
        api_key = os.environ.get('TMDB_API_KEY')
        series_id = get_series_id(api_key, show_name)

        if series_id:
            details_url = f"https://api.themoviedb.org/3/tv/{series_id}?api_key={api_key}&language={language}"
            details_response = requests.get(details_url, timeout=10)
            details_response.raise_for_status()
            details_data = details_response.json()

            logger.info(f"TMDb data found for: {show_name}")
            return {
                'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get(
                    'poster_path') else None,
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
        logger.exception(f"An unexpected error occurred: {e}")
        return None


# --- Celery Setup and Task Definition (combined)---
def make_celery(app):
    celery = Celery(
        app.import_name,
        broker=app.config['REDIS_URL'],
        backend=app.config['REDIS_URL']
    )
    celery.conf.update(app.config)

    # Define the update_tv_shows task *inside* make_celery
    @celery.task(bind=True, retry_backoff=True)
    def update_tv_shows(self, event_data):
        """Celery task to process a new message event and update the database."""
        try:
            message_id, show_name, episode_title, download_link = (
                event_data['message_id'],
                event_data['show_name'],
                event_data['episode_title'],
                event_data['download_link'],
            )
            tmdb_data = fetch_tmdb_data(show_name)
            show_data = {
                'show_name': show_name,
                'episode_title': episode_title,
                'download_link': download_link,
                'message_id': message_id,
                'overview': tmdb_data.get('overview') if tmdb_data else None,
                'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                'poster_path': tmdb_data.get('poster_path') if tmdb_data else None,
            }
            with app.app_context():
                existing_show = TVShow.query.filter_by(message_id=message_id).first()
                if existing_show:
                    for key, value in show_data.items():
                        setattr(existing_show, key, value)
                    db.session.commit()
                    logger.info(f"Successfully updated: {show_name} - {episode_title}")
                else:
                    new_show = TVShow(**show_data)
                    db.session.add(new_show)
                    db.session.commit()
                    logger.info(f"Successfully inserted: {show_name} - {episode_title}")
                db.session.remove()  # Added session removal

        except MaxRetriesExceededError:
            logger.error("Max retries exceeded for update_tv_shows task.")
        except Exception as e:
            logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
            self.retry(exc=e, countdown=60)

    @celery.task
    def run_telethon_client():
        """Celery task to run the Telethon client (bot account)."""
        try:
            # Use the bot token for authentication
            client = TelegramClient(StringSession(), API_ID, API_HASH).start(bot_token=BOT_TOKEN)

            @client.on(events.NewMessage(chats=[TELEGRAM_CHANNEL_ID]))
            async def new_message_listener(event):
                await process_telegram_message(event)

            with client:
                logger.info("Telethon client (bot) started. Listening for new messages...")
                client.run_until_disconnected()

        except Exception as e:
            logger.exception(f"Error in Telethon client: {e}")

    # Return the celery object *and* the tasks (so they're accessible)
    return celery

async def process_telegram_message(event):
    if event.message.sender_chat and event.message.sender_chat.id == TELEGRAM_CHANNEL_ID:
        parsed_data = parse_telegram_post(event)
        if parsed_data:
            show_name = parsed_data['show_name']
            episode_title = parsed_data.get('season_episode')
            tmdb_api_key = os.environ.get('TMDB_API_KEY')

            if not episode_title:
                series_id = get_series_id(tmdb_api_key, show_name)
                if series_id:
                    season_count = get_season_count(tmdb_api_key, series_id)
                    if season_count > 0:
                        episode_title, _ = get_latest_episode(tmdb_api_key, series_id, season_count)

            if show_name and episode_title:
                event_data = {
                    'message_id': parsed_data['message_id'],
                    'show_name': show_name,
                    'episode_title': episode_title,
                    'download_link': parsed_data['download_link'],
                }
                make_celery(app).tasks['tv_app.tasks.update_tv_shows'].delay(event_data)



# --- Simple test task ---
@celery.task
def test_task():
    logger.info("The test Celery task has run!")
    return "Test task complete"
