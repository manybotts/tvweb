import os
import re
from flask import Flask, render_template, redirect, url_for, g, request
from pymongo import MongoClient, ASCENDING, DESCENDING
import logging
from dotenv import load_dotenv
# IMPORTANT: Import the *task* from tasks.py, not the whole module
from tasks import update_tv_shows, test_task
from datetime import datetime, timezone #For sorting

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['MONGO_URI'] = os.environ.get('MONGO_URI')
app.config['DATABASE_NAME'] = os.environ.get('MONGO_DATABASE_NAME', 'tv_shows')
# Removed Telegram and TMDB configs from here - now in tasks.py

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

# --- Database Operations (MongoDB) --- (Keep these for Flask)---

def get_all_tv_shows(page=1, per_page=9, search_query=None):
    """Retrieves TV shows with pagination and search."""
    db = get_db()
    offset = (page - 1) * per_page
    query = {}

    if search_query:
        regex_query = re.compile(f".*{re.escape(search_query)}.*", re.IGNORECASE)
        query['show_name'] = {'$regex': regex_query}

    total_shows = db.tv_shows.count_documents(query)
    tv_shows_cursor = db.tv_shows.find(query).sort('created_at', DESCENDING).skip(offset).limit(per_page) # Sort the shows with created_at
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
    show_names_cursor = db.tv_shows.distinct('show_name')
    show_names = list(show_names_cursor)
    return show_names

# --- Routes ---

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 9

    logger.info("About to enqueue update_tv_shows task")
    update_tv_shows.delay()
    logger.info("update_tv_shows task enqueued")

    tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query)

@app.route('/show/<int:message_id>')
def show_details(message_id):
    """Displays details for a single TV show."""
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
    """Displays a list of all available TV show names."""
    show_names = get_all_show_names()
    return render_template('shows.html', show_names=show_names)

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
