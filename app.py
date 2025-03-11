import os
import re
import sqlite3
from flask import Flask, render_template, redirect, url_for, g
import requests
from telegram import Bot
from telegram.error import TelegramError
import asyncio

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')  # Use environment variable
app.config['TELEGRAM_BOT_TOKEN'] = os.environ.get('TELEGRAM_BOT_TOKEN')
app.config['TMDB_API_KEY'] = os.environ.get('TMDB_API_KEY')
app.config['TELEGRAM_CHANNEL_ID'] = os.environ.get('TELEGRAM_CHANNEL_ID')
app.config['DATABASE'] = 'tv_shows.db'  # Database file

# Check for required environment variables
if not all([app.config['TELEGRAM_BOT_TOKEN'], app.config['TMDB_API_KEY'], app.config['TELEGRAM_CHANNEL_ID']]):
    raise ValueError("Missing required environment variables: TELEGRAM_BOT_TOKEN, TMDB_API_KEY, or TELEGRAM_CHANNEL_ID")

# --- Database Setup ---

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row  # Access columns by name
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        # Check if the table already exists
        cursor = db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tv_shows'")
        table_exists = cursor.fetchone()

        if not table_exists:
            with app.open_resource('schema.sql', mode='r') as f:
                db.cursor().executescript(f.read())
            db.commit()
            print("Database initialized.")  # Add a log message
        else:
            print("Database table 'tv_shows' already exists.")

# --- Helper Functions ---

def fetch_telegram_posts():
    """Fetches recent posts from the Telegram channel."""
    try:
        bot = Bot(token=app.config['TELEGRAM_BOT_TOKEN'])
        async def get_updates():
            #Allow channel posts, and media types.
          updates = await bot.get_updates(allowed_updates=['channel_post'])
          return updates
        updates = asyncio.run(get_updates())
        posts = []
        for update in updates:
            if update.channel_post and (update.channel_post.caption or update.channel_post.text): #Check caption
                posts.append(update.channel_post)
        return posts
    except TelegramError as e:
        print(f"Error fetching Telegram posts: {e}")
        return []

def parse_telegram_post(post):
    """Parses a Telegram post (caption of media) to extract show info."""
    try:
        # Check if post.caption exists and is not None
        if post.caption:
            text = post.caption  # Use post.caption instead of post.text
            match = re.search(r"^(.*?)\n(Season\s+\d+(?:\s*-\s*\d+)?(?:,\s*Episode\s+\d+(?:\s*-\s*\d+)?)?)\n.*?CLICK HERE", text, re.DOTALL | re.IGNORECASE)
            if match:
                show_name = match.group(1).strip()
                season_episode = match.group(2).strip()
                link_match = re.search(r"(https?://[^\s]+)", text)
                download_link = link_match.group(1).strip() if link_match else None
                return {
                    'show_name': show_name,
                    'season_episode': season_episode,
                    'download_link': download_link,
                    'message_id': post.message_id
                }
        else:
            print(f"Skipping post with ID {post.message_id}: No caption content.") #Log if no caption
        return None  # Return None if post.caption is None or no match is found
    except Exception as e:
        print(f"Error parsing post: {e}")
        return None

def fetch_tmdb_data(show_name, language='en-US'):
    """Fetches TV show data from TMDb."""
    try:
        search_url = f"https://api.themoviedb.org/3/search/tv?api_key={app.config['TMDB_API_KEY']}&query={show_name}&language={language}"
        search_response = requests.get(search_url)
        search_data = search_response.json()

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?api_key={app.config['TMDB_API_KEY']}&language={language}"
            details_response = requests.get(details_url)
            details_data = details_response.json()

            return {
                'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None,
                'overview': details_data.get('overview'),
                'vote_average': details_data.get('vote_average'),
            }
        return None
    except Exception as e:
        print(f"Error fetching TMDb data: {e}")
        return None

def update_tv_shows():
    """Fetches new posts and updates the database."""
    posts = fetch_telegram_posts()
    db = get_db()
    for post in reversed(posts):
        parsed_data = parse_telegram_post(post)
        if parsed_data:
            existing_show = db.execute('SELECT * FROM tv_shows WHERE message_id = ?', (parsed_data['message_id'],)).fetchone()
            if not existing_show:
                tmdb_data = fetch_tmdb_data(parsed_data['show_name'], language='en-US')
                if tmdb_data:
                    db.execute('''
                        INSERT INTO tv_shows (message_id, show_name, season_episode, download_link, poster_path, overview, vote_average)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (parsed_data['message_id'], parsed_data['show_name'], parsed_data['season_episode'],
                          parsed_data['download_link'], tmdb_data['poster_path'], tmdb_data['overview'],
                          tmdb_data['vote_average']))
                    db.commit()

def get_all_tv_shows():
    """Retrieves all TV shows from the database."""
    db = get_db()
    cur = db.execute('SELECT * FROM tv_shows ORDER BY message_id DESC')
    return cur.fetchall()

def get_tv_show_by_message_id(message_id):
    """Retrieves a single TV show by its message_id."""
    db = get_db()
    cur = db.execute('SELECT * FROM tv_shows WHERE message_id = ?', (message_id,))
    return cur.fetchone()

# --- Routes ---

@app.route('/')
def index():
    update_tv_shows()
    tv_shows = get_all_tv_shows()
    return render_template('index.html', tv_shows=tv_shows)

@app.route('/show/<int:message_id>')
def show_details(message_id):
    update_tv_shows()
    show = get_tv_show_by_message_id(message_id)
    if show:
        return render_template('show_details.html', show=show)
    return "Show not found", 404

@app.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    update_tv_shows()
    show = get_tv_show_by_message_id(message_id)
    if show and show['download_link']:
        return redirect(show['download_link'])
    return "Show or link not found", 404

# Initialize the database (outside the if __name__ == '__main__': block)
init_db()

if __name__ == '__main__':
    #init_db()  # Don't call it here anymore
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
