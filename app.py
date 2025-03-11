import os
import re
import requests
from flask import Flask, render_template, redirect, url_for, g, request
from telegram import Bot
from telegram.error import TelegramError
import asyncio
from urllib.parse import quote_plus
from pymongo import MongoClient

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['TELEGRAM_BOT_TOKEN'] = os.environ.get('TELEGRAM_BOT_TOKEN')
app.config['TMDB_API_KEY'] = os.environ.get('TMDB_API_KEY')
app.config['TELEGRAM_CHANNEL_ID'] = os.environ.get('TELEGRAM_CHANNEL_ID')
app.config['MONGO_URI'] = os.environ.get('MONGO_URI')
app.config['DATABASE_NAME'] = os.environ.get('MONGO_DATABASE_NAME', 'tv_shows') # Added default

if not all([app.config['TELEGRAM_BOT_TOKEN'], app.config['TMDB_API_KEY'], app.config['TELEGRAM_CHANNEL_ID'], app.config['MONGO_URI']]):
    raise ValueError("Missing required environment variables")

# --- Database Setup (MongoDB) ---

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        client = MongoClient(app.config['MONGO_URI'])
        db = g._database = client[app.config['DATABASE_NAME']]  # Use database name from config
        try:
            # Attempt a simple command to check the connection
            db.command('ping')
            print("Successfully connected to MongoDB!")
        except Exception as e:
            print(f"Error connecting to MongoDB: {e}")
            raise  # Re-raise the exception
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.client.close()

# --- Helper Functions ---

def fetch_telegram_posts():
    """Fetches recent posts from the Telegram channel."""
    try:
        bot = Bot(token=app.config['TELEGRAM_BOT_TOKEN'])
        async def get_updates():
          updates = await bot.get_updates(allowed_updates=['channel_post'])
          return updates
        updates = asyncio.run(get_updates())
        posts = []
        print(f"Raw updates from Telegram: {updates}")  # KEEP THIS
        for update in updates:
            print(f"Processing update: {update}") # KEEP THIS
            if update.channel_post:
                print(f"  Channel post found: {update.channel_post}") # KEEP THIS
                if update.channel_post.caption:
                    print(f"    Caption found: {update.channel_post.caption}") # KEEP THIS
                    posts.append(update.channel_post)
                elif update.channel_post.text:
                    print(f"    Text found: {update.channel_post.text}") # KEEP THIS
                    posts.append(update.channel_post)
                else:
                    print("    No caption or text found in channel post.") # KEEP THIS
            else:
                print("  Not a channel post.") # KEEP THIS
        print(f"Posts found: {posts}")  # KEEP THIS
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
            print(f"Parsing post caption: {text}")  # KEEP THIS
            match = re.search(r"^(.*?)\n(Season\s+\d+.*)\n(.*?)HERE", text, re.DOTALL | re.IGNORECASE)
            if match:
                show_name = match.group(1).strip()
                season_episode = match.group(2).strip()
                link_text = match.group(3).strip()  # Capture text *before* "HERE"

                download_link = None  # Initialize to None
                if post.caption_entities:
                  for entity in post.caption_entities:
                      print(f"  Entity: {entity}")  # KEEP THIS
                      if entity.type == 'text_link' and text[entity.offset:entity.offset+entity.length] == "HERE ✔️":
                        download_link = entity.url
                        print(f"    Found text_link URL: {download_link}")  # KEEP THIS
                        break  # Stop after finding the first matching text_link

                print(f"Parsed data: show_name={show_name}, season_episode={season_episode}, download_link={download_link}, link_text={link_text}")  # KEEP THIS
                return {
                    'show_name': show_name,
                    'season_episode': season_episode,
                    'download_link': download_link,
                    'message_id': post.message_id
                }
            else:
                print(f"Regex did not match for caption: {text}") #Log
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
        print(f"TMDb search data for '{show_name}': {search_data}")  # KEEP THIS

        if search_data['results']:
            show_id = search_data['results'][0]['id']
            details_url = f"https://api.themoviedb.org/3/tv/{show_id}?api_key={app.config['TMDB_API_KEY']}&language={language}"
            details_response = requests.get(details_url)
            details_data = details_response.json()
            print(f"TMDb details data for show ID {show_id}: {details_data}")  # KEEP THIS

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
    print(f"Connected to MongoDB database: {db.name}")  # Add this
    for post in posts:
        parsed_data = parse_telegram_post(post)
        if parsed_data:
            print(f"Checking if show exists with message_id: {parsed_data['message_id']}")  # Add this
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
                print(f"Inserting show data: {show_data}")  # Add this
                result = db.tv_shows.insert_one(show_data)
                print(f"Inserted document ID: {result.inserted_id}")  # Add this
            else:
                print(f"Show already exists with message_id: {parsed_data['message_id']}")


def get_all_tv_shows(page=1, per_page=9, search_query=None):
    """Retrieves TV shows with pagination and search."""
    db = get_db()
    offset = (page - 1) * per_page
    query = {}  # Start with an empty query

    if search_query:
        # Use a regex for case-insensitive partial matching
        regex_query = re.compile(f".*{re.escape(search_query)}.*", re.IGNORECASE)
        query = {'show_name': {'$regex': regex_query}}

    # Count total matching documents for pagination (important for correct total_pages)
    total_shows = db.tv_shows.count_documents(query)

    # Fetch shows for the current page, applying sort, skip, and limit
    tv_shows_cursor = db.tv_shows.find(query).sort('message_id', -1).skip(offset).limit(per_page)
    tv_shows = list(tv_shows_cursor)  # Convert cursor to a list

    total_pages = (total_shows + per_page - 1) // per_page

    return tv_shows, total_pages

def get_all_show_names():
    """Retrieves a list of all unique show names."""
    db = get_db()
    show_names_cursor = db.tv_shows.distinct('show_name')  # Use distinct
    show_names = list(show_names_cursor)
    return show_names

def get_tv_show_by_message_id(message_id):
    """Retrieves a single TV show by its message_id."""
    db = get_db()
    show = db.tv_shows.find_one({'message_id': message_id})
    return show

# --- Routes ---

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 9  # Moved inside the route function
    update_tv_shows()  # Update data
    tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)  # Get data and total pages
    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query)


@app.route('/show/<int:message_id>')
def show_details(message_id):
    """Displays details for a single TV show."""
    update_tv_shows()
    show = get_tv_show_by_message_id(message_id)
    if show:
      return render_template('show_details.html', show=show)
    else:
      return "Show not found", 404


@app.route('/shows')
def list_shows():
    """Displays a list of all available TV show names."""
    show_names = get_all_show_names()
    return render_template('shows.html', show_names=show_names)

@app.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    """Redirects to the download link for a TV show."""
    update_tv_shows()
    show = get_tv_show_by_message_id(message_id)
    if show and show.get('download_link'):
        return redirect(show['download_link'])
    return "Show or link not found", 404


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
