import os
import re
import sqlite3
from flask import Flask, render_template, redirect, url_for, g, request
import requests
from telegram import Bot
from telegram.error import TelegramError
import asyncio
from PIL import Image
import io
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['TELEGRAM_BOT_TOKEN'] = os.environ.get('TELEGRAM_BOT_TOKEN')
app.config['TMDB_API_KEY'] = os.environ.get('TMDB_API_KEY')
app.config['TELEGRAM_CHANNEL_IDS'] = os.environ.get('TELEGRAM_CHANNEL_IDS', '')
app.config['DATABASE'] = 'tv_shows.db'  # Back to SQLite

if not all([app.config['TELEGRAM_BOT_TOKEN'], app.config['TMDB_API_KEY'], app.config['TELEGRAM_CHANNEL_IDS']]):
    raise ValueError("Missing required environment variables")

# --- Database Setup (SQLite) ---

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()

# --- Helper Functions ---
async def fetch_telegram_posts():
    """Fetches recent posts from all configured Telegram channels."""
    try:
        bot = Bot(token=app.config['TELEGRAM_BOT_TOKEN'])
        all_posts = []
        channel_ids_str = app.config['TELEGRAM_CHANNEL_IDS']
        channel_ids = [cid.strip() for cid in channel_ids_str.split(',') if cid.strip()]

        async def get_updates_for_channel(channel_id):
            updates = await bot.get_updates(allowed_updates=['channel_post'], timeout=60, offset=None)
            channel_posts = []
            for update in updates:
                if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == str(channel_id):
                    if update.channel_post.caption or update.channel_post.text:
                        channel_posts.append(update.channel_post)
            return channel_posts

        for channel_id in channel_ids:
            try:
                posts = await get_updates_for_channel(channel_id)
                all_posts.extend(posts)
            except TelegramError as e:
                logger.error(f"Error fetching posts from channel {channel_id}: {e}")
                continue
        return all_posts

    except Exception as e:
        logger.exception(f"An unexpected error occurred in fetch_telegram_posts: {e}")
        return []

def parse_telegram_post(post):
    """Parses a Telegram post (caption) to extract show info."""
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
        logger.error(f"Error parsing post: {e}")
        return None

def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb, resizes, and saves as WebP."""
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

            poster_path = details_data.get('poster_path')
            if poster_path:
                image_url = f"https://image.tmdb.org/t/p/original{poster_path}"
                image_response = requests.get(image_url, stream=True)
                image_response.raise_for_status()

                image = Image.open(io.BytesIO(image_response.content))

                # --- Multiple Sizes ---
                sizes = [220, 440]  # Example: Two sizes
                poster_paths = {}
                for size in sizes:
                    w_percent = (size / float(image.size[0]))
                    h_size = int((float(image.size[1]) * float(w_percent)))
                    thumbnail = image.resize((size, h_size), Image.LANCZOS)

                    thumb_io = io.BytesIO()
                    thumbnail.save(thumb_io, 'WEBP', quality=80)
                    thumb_io.seek(0)
                    # Corrected path: Remove the leading slash.
                    webp_path = f"static/posters/{show_id}-{size}.webp"
                    poster_paths[f'poster_path_{size}'] = webp_path

                    # Save to the file system.  Make sure the directory exists!
                    os.makedirs(os.path.join(app.root_path, 'static', 'posters'), exist_ok=True)
                    with open(os.path.join(app.root_path, webp_path), 'wb') as f:
                        f.write(thumb_io.read())
                # --- End Multiple Sizes ---

                return {
                    'poster_path': poster_paths['poster_path_220'],  # Default
                    **poster_paths, # Include all paths
                    'overview': details_data.get('overview'),
                    'vote_average': details_data.get('vote_average'),
                }
            else:
                return {
                    'poster_path': None,
                    'overview': details_data.get('overview'),
                    'vote_average': details_data.get('vote_average'),
                }
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching data from TMDb: {e}")
        return None
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return None

# --- Database Operations (SQLite) ---
async def async_update_tv_shows():
    """Fetches new posts and updates the database (async version)."""
    posts = await fetch_telegram_posts()
    if not posts:
        return

    db = get_db()
    for post in posts:
        parsed_data = parse_telegram_post(post)
        if parsed_data:
            tmdb_data = fetch_tmdb_data(parsed_data['show_name'])
            if tmdb_data:
                # Prepare data for insertion
                show_data = {
                    'show_name': parsed_data['show_name'],
                    'season_episode': parsed_data['season_episode'],
                    'download_link': parsed_data['download_link'],
                    'message_id': parsed_data['message_id'],
                    'overview': tmdb_data.get('overview'),
                    'vote_average': tmdb_data.get('vote_average'),
                    'poster_path': tmdb_data.get('poster_path')
                }
                # Check if the show already exists
                existing_show = db.execute('SELECT * FROM tv_shows WHERE show_name = ?', (parsed_data['show_name'],)).fetchone()

                if existing_show:
                    # Update existing show
                    db.execute('''
                        UPDATE tv_shows
                        SET season_episode = ?, download_link = ?, message_id = ?, overview = ?, vote_average = ?, poster_path = ?
                        WHERE show_name = ?
                    ''', (show_data['season_episode'], show_data['download_link'], show_data['message_id'],
                          show_data['overview'], show_data['vote_average'], show_data['poster_path'], show_data['show_name']))
                else:
                    # Insert new show
                    db.execute('''
                        INSERT INTO tv_shows (show_name, season_episode, download_link, message_id, overview, vote_average, poster_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (show_data['show_name'], show_data['season_episode'], show_data['download_link'],
                          show_data['message_id'], show_data['overview'], show_data['vote_average'], show_data['poster_path']))
                db.commit()


def get_all_tv_shows(page=1, per_page=9, search_query=None):
    db = get_db()
    offset = (page - 1) * per_page
    query = 'SELECT * FROM tv_shows'
    params = []

    if search_query:
        query += ' WHERE show_name LIKE ?'
        params.append('%' + search_query + '%')

    query += ' ORDER BY message_id DESC LIMIT ? OFFSET ?'
    params.extend([per_page, offset])

    cur = db.execute(query, params)
    tv_shows = cur.fetchall()

    # Get total count for pagination
    count_query = 'SELECT COUNT(*) FROM tv_shows'
    count_params = []
    if search_query:
        count_query += ' WHERE show_name LIKE ?'
        count_params = ['%' + search_query + '%']

    count_cur = db.execute(count_query, count_params)
    total_shows = count_cur.fetchone()[0]
    total_pages = (total_shows + per_page - 1) // per_page

    return tv_shows, total_pages

def get_tv_show_by_message_id(message_id):
    db = get_db()
    cur = db.execute('SELECT * FROM tv_shows WHERE message_id = ?', (message_id,))
    return cur.fetchone()

def get_all_show_names():
    db = get_db()
    cur = db.execute('SELECT DISTINCT show_name FROM tv_shows ORDER BY show_name')
    return [row['show_name'] for row in cur.fetchall()]

# --- Routes ---
LAST_UPDATE_TIME = 0  # Global variable to store the last update time
UPDATE_INTERVAL = 300  # Update every 300 seconds (5 minutes)

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search."""
    global LAST_UPDATE_TIME
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 9
    current_time = time.time()
    if current_time - LAST_UPDATE_TIME > UPDATE_INTERVAL:
        asyncio.run(async_update_tv_shows())
        LAST_UPDATE_TIME = current_time
    tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query)

@app.route('/show/<int:message_id>')
def show_details(message_id):
    show = get_tv_show_by_message_id(message_id)
    if show:
        return render_template('show_details.html', show=show)
    return "Show not found", 404

@app.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    show = get_tv_show_by_message_id(message_id)
    if show and show.get('download_link'):
        return redirect(show['download_link'])
    return "Show or link not found", 404

@app.route('/shows')
def list_shows():
    show_names = get_all_show_names()
    return render_template('shows.html', show_names=show_names)

# Initialize the database before the first request
@app.before_first_request
def initialize_database():
    init_db()

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
