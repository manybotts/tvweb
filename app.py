import os
import re
import requests
from flask import Flask, render_template, redirect, url_for, g, request
from telegram import Bot
from telegram.error import TelegramError
import asyncio
from urllib.parse import quote_plus
from pymongo import MongoClient  # Import MongoClient

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['TELEGRAM_BOT_TOKEN'] = os.environ.get('TELEGRAM_BOT_TOKEN')
app.config['TMDB_API_KEY'] = os.environ.get('TMDB_API_KEY')
app.config['TELEGRAM_CHANNEL_ID'] = os.environ.get('TELEGRAM_CHANNEL_ID')
app.config['MONGO_URI'] = os.environ.get('MONGO_URI')  # Get MongoDB URI from environment
app.config['DATABASE_NAME'] = os.environ.get('MONGO_DATABASE_NAME', 'tv_shows') #Get DB name

if not all([app.config['TELEGRAM_BOT_TOKEN'], app.config['TMDB_API_KEY'], app.config['TELEGRAM_CHANNEL_ID'], app.config['MONGO_URI']]):
    raise ValueError("Missing required environment variables")

# --- Database Setup (MongoDB) ---

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        client = MongoClient(app.config['MONGO_URI'])
        db = g._database = client[app.config['DATABASE_NAME']]  # Use get_database() and config variable
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.client.close()  # Close the client connection

# --- Helper Functions --- (No changes needed here)

def fetch_telegram_posts():
    """Fetches recent posts from the Telegram channel."""
    try:
        bot = Bot(token=app.config['TELEGRAM_BOT_TOKEN'])
        async def get_updates():
          updates = await bot.get_updates(allowed_updates=['channel_post'])
          return updates
        updates = asyncio.run(get_updates())
        posts = []
        for update in updates:
            if update.channel_post and (update.channel_post.caption or update.channel_post.text):
                posts.append(update.channel_post)
        return posts
    except TelegramError as e:
        print(f"Error fetching Telegram posts: {e}")
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
                    'message_id': post.message_id
                }
        return None
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

# --- Database Operations (MongoDB) ---
def update_tv_shows():
    """Fetches new posts and updates the database."""
    posts = fetch_telegram_posts()
    if not posts:
        return

    db = get_db()
    for post in posts:
        parsed_data = parse_telegram_post(post)
        if parsed_data:
            # Check if the show already exists using message_id
            existing_show = db.tv_shows.find_one({'message_id': parsed_data['message_id']})
            if not existing_show:
                tmdb_data = fetch_tmdb_data(parsed_data['show_name'])
                show_data = {
                    'message_id': parsed_data['message_id'],
                    'show_name': parsed_data['show_name'],
                    'season_episode': parsed_data['season_episode'],
                    'download_link': parsed_data['download_link'],
                    'overview': tmdb_data.get('overview') if tmdb_data else None,
                    'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                    'poster_path': tmdb_data.get('poster_path') if tmdb_data else None
                }
                # Insert the new show
                db.tv_shows.insert_one(show_data)


# --- Routes ---

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 12
    db = get_db()
    if search_query:
        # Use a regex for case-insensitive partial matching
        regex_query = re.compile(f".*{re.escape(search_query)}.*", re.IGNORECASE)
        query = {'show_name': {'$regex': regex_query}}
        # Count total matching documents for pagination
        total_shows = db.tv_shows.count_documents(query)
        # Fetch shows for the current page
        tv_shows_cursor = db.tv_shows.find(query).sort('message_id', -1).skip((page - 1) * per_page).limit(per_page)

    else:
        # Count total documents for pagination
        total_shows = db.tv_shows.count_documents({})
        # Fetch shows for the current page
        tv_shows_cursor = db.tv_shows.find().sort('message_id', -1).skip((page - 1) * per_page).limit(per_page)

    tv_shows = list(tv_shows_cursor)  # Convert cursor to a list
    total_pages = (total_shows + per_page - 1) // per_page

    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query)


@app.route('/show/<int:message_id>')
def show_details(message_id):
    """Displays details for a single TV show."""
    db = get_db()
    show = db.tv_shows.find_one({'message_id': message_id})
    if show:
      return render_template('show_details.html', show=show)
    else:
      return "Show not found", 404


@app.route('/shows')
def list_shows():
    """Displays a list of all available TV show names."""
    db = get_db()
    show_names_cursor = db.tv_shows.distinct('show_name')  # Use distinct
    show_names = list(show_names_cursor)
    return render_template('shows.html', show_names=show_names)

@app.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    """Redirects to the download link for a TV show."""
    db = get_db()
    show = db.tv_shows.find_one({'message_id': message_id})
    if show and show.get('download_link'):
        return redirect(show['download_link'])
    return "Show or link not found", 404

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
