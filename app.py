import os
import re
import requests
from flask import Flask, render_template, redirect, url_for, g, request
from telegram import Bot
from telegram.error import TelegramError
import asyncio
from urllib.parse import quote_plus
from pymongo import MongoClient, ASCENDING, DESCENDING
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['TELEGRAM_BOT_TOKEN'] = os.environ.get('TELEGRAM_BOT_TOKEN')
app.config['TMDB_API_KEY'] = os.environ.get('TMDB_API_KEY')
app.config['TELEGRAM_CHANNEL_IDS'] = os.environ.get('TELEGRAM_CHANNEL_IDS', '') # Get comma separated channel IDs
app.config['MONGO_URI'] = os.environ.get('MONGO_URI')
app.config['DATABASE_NAME'] = os.environ.get('MONGO_DATABASE_NAME', 'tv_shows')

if not all([app.config['TELEGRAM_BOT_TOKEN'], app.config['TMDB_API_KEY'], app.config['MONGO_URI'], app.config['TELEGRAM_CHANNEL_IDS']]):
    raise ValueError("Missing required environment variables")

# --- Database Setup (MongoDB) ---

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        client = MongoClient(app.config['MONGO_URI'])
        db = g._database = client[app.config['DATABASE_NAME']]
        try:
            db.command('ping')
            logger.info("Successfully connected to MongoDB!")
        except Exception as e:
            logger.error(f"Error connecting to MongoDB: {e}")
            raise
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.client.close()

# --- Helper Functions ---
async def fetch_telegram_posts():
    """Fetches recent posts from all configured Telegram channels."""
    try:
        bot = Bot(token=app.config['TELEGRAM_BOT_TOKEN'])
        all_posts = []
        channel_ids_str = app.config['TELEGRAM_CHANNEL_IDS']
        channel_ids = [cid.strip() for cid in channel_ids_str.split(',') if cid.strip()]

        async def get_updates_for_channel(channel_id):
            # No asyncio.run() here!  Use await directly.
            updates = await bot.get_updates(allowed_updates=['channel_post'], timeout=60, offset=None)
            channel_posts = []
            for update in updates:
                if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == str(channel_id):
                    if update.channel_post.caption or update.channel_post.text:
                        channel_posts.append(update.channel_post)
            return channel_posts

        for channel_id in channel_ids:
            try:
                posts = await get_updates_for_channel(channel_id)  # Await the coroutine
                all_posts.extend(posts)
            except TelegramError as e:
                logger.error(f"Error fetching posts from channel {channel_id}: {e}")
                continue

        return all_posts  #Return all the posts

    except Exception as e:
        logger.exception(f"An unexpected error occurred in fetch_telegram_posts: {e}")
        return []

def parse_telegram_post(post):
    """Parses a Telegram post (caption of media) to extract show info."""
    try:
        if post.caption:
            text = post.caption
            match = re.search(r"^(.*?)\n(Season\s+\d+.*)\n(.*?)HERE", text, re.DOTALL | re.IGNORECASE)
            if match:
                show_name = match.group(1).strip()
                season_episode = match.group(2).strip()
                link_text = match.group(3).strip()

                download_link = None
                if post.caption_entities:
                  for entity in post.caption_entities:
                      if entity.type == 'text_link' and text[entity.offset:entity.offset+entity.length] == "HERE ✔️":
                        download_link = entity.url
                        break

                return {
                    'show_name': show_name,
                    'season_episode': season_episode,
                    'download_link': download_link,
                    'message_id': post.message_id,
                }
        return None
    except Exception as e:
        logger.error(f"Error parsing post: {e}")  # Use logger for consistency
        return None

def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb."""
    try:
        search_url = f"https://api.themoviedb.org/3/search/tv?api_key={app.config['TMDB_API_KEY']}&query={show_name}&language={language}"
        search_response = requests.get(search_url)
        search_response.raise_for_status()
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?api_key={app.config['TMDB_API_KEY']}&language={language}"
            details_response = requests.get(details_url)
            details_response.raise_for_status()
            details_data = details_response.json()

            return {
                'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
                'overview': details_data.get('overview'),
                'vote_average': details_data.get('vote_average'),
            }
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return None
# --- Database Operations (MongoDB) ---
async def update_tv_shows():
    """Fetches new posts and updates the database (async version)."""
    posts = await fetch_telegram_posts()  # Await the fetch
    if not posts:
        return

    db = get_db()  # Get the database connection (this is still synchronous)
    for post in posts:
        parsed_data = parse_telegram_post(post)
        if parsed_data:
            tmdb_data = fetch_tmdb_data(parsed_data['show_name'])
            show_data = {
                'show_name': parsed_data['show_name'],  # Use show_name as the key
                'season_episode': parsed_data['season_episode'],
                'download_link': parsed_data['download_link'],
                'message_id': parsed_data['message_id'],
                'overview': tmdb_data.get('overview') if tmdb_data else None,
                'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                'poster_path': tmdb_data.get('poster_path') if tmdb_data else None,
            }
            # Use update_one with upsert=True.
            db.tv_shows.update_one(
                {'show_name': parsed_data['show_name']},  # Find by show_name
                {'$set': show_data},  # Update or set these fields
                upsert=True  # Insert if it doesn't exist
            )
    #Ensure we have indexes
    db.tv_shows.create_index([("show_name", ASCENDING)], unique=True)
    db.tv_shows.create_index([("message_id", ASCENDING)])

def get_all_tv_shows(page=1, per_page=9, search_query=None):
    """Retrieves TV shows with pagination and search."""
    db = get_db()
    offset = (page - 1) * per_page
    query = {}

    if search_query:
        regex_query = re.compile(f".*{re.escape(search_query)}.*", re.IGNORECASE)
        query['show_name'] = {'$regex': regex_query}

    total_shows = db.tv_shows.count_documents(query)
    tv_shows_cursor = db.tv_shows.find(query).sort('message_id', -1).skip(offset).limit(per_page)
    tv_shows = list(tv_shows_cursor)
    total_pages = (total_shows + per_page - 1) // per_page

    return tv_shows, total_pages

def get_tv_show_by_message_id(message_id):
    """Retrieves a single TV show by its message_id."""
    db = get_db()
    show = db.tv_shows.find_one({'message_id': message_id})
    return show

def get_all_show_names():
    """Retrieves a list of all unique show names."""
    db = get_db()
    show_names_cursor = db.tv_shows.distinct('show_name')  # Use distinct
    show_names = list(show_names_cursor)
    return show_names
# --- Routes ---

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 9
    asyncio.run(update_tv_shows())  # Update data using asyncio.run
    tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query)

@app.route('/show/<int:message_id>')
def show_details(message_id):
    """Displays details for a single TV show."""
    asyncio.run(update_tv_shows()) # Update data using asyncio.run
    show = get_tv_show_by_message_id(message_id)
    if show:
        return render_template('show_details.html', show=show)
    return "Show not found", 404

@app.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    """Redirects to the download link for a TV show."""
    show = get_tv_show_by_message_id(message_id)
    if show and show.get('download_link'):
        return redirect(show['download_link'])
    return "Show or link not found", 404
  
@app.route('/shows')
def list_shows():
    show_names = get_all_show_names()
    return render_template('shows.html', show_names=show_names)

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
