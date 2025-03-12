import os
import re
from flask import Flask, render_template, redirect, url_for, request
import logging
from dotenv import load_dotenv
from .tasks import update_tv_shows  # Import Celery task
from .models import db, TVShow  # Import from models.py
from sqlalchemy import desc

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')

# --- CORRECT DATABASE CONFIGURATION (Simplified) ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')  # Use Railway's provided URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Suppress a warning

db.init_app(app)  # Initialize db with the app

# --- Database Operations ---

def get_all_tv_shows(page=1, per_page=10, search_query=None):
    """Retrieves TV shows with pagination and search."""
    logger.info(f"get_all_tv_shows called: page={page}, per_page={per_page}, search_query={search_query!r}")  # Log inputs
    offset = (page - 1) * per_page
    query = TVShow.query

    if search_query:
        logger.info(f"Applying search filter: {search_query!r}")
        query = query.filter(TVShow.show_name.ilike(f"%{search_query}%"))

    # Log the generated SQL query (VERY HELPFUL)
    logger.info(f"Generated SQL query: {query.statement}")

    total_shows = query.count()
    logger.info(f"Total shows matching query: {total_shows}")

    tv_shows = query.order_by(TVShow.created_at.desc()).offset(offset).limit(per_page).all()
    logger.info(f"Retrieved shows: {tv_shows}")  # Log the actual show objects

    total_pages = (total_shows + per_page - 1) // per_page
    return tv_shows, total_pages


def get_tv_show_by_message_id(message_id):
    """Retrieves a single TV show by its message_id."""
    return TVShow.query.filter_by(message_id=message_id).first()

def get_all_show_names():
    """Retrieves a list of all unique show names."""
    return [show.show_name for show in TVShow.query.distinct(TVShow.show_name).order_by(TVShow.show_name).all()]

def get_trending_shows(limit=5):
    """Retrieves the top 'limit' trending shows, ordered by clicks."""
    return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

# --- Routes ---

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search, plus trending shows."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    logger.info("About to enqueue update_tv_shows task")
    update_tv_shows.delay()  # Enqueue the Celery task
    logger.info("update_tv_shows task enqueued")

    if search_query:
        # If there's a search query, *only* fetch the matching shows.
        tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
        trending_shows = []  # Don't fetch trending shows if searching
    else:
        # If there's *no* search query, fetch both all shows and trending shows.
        tv_shows, total_pages = get_all_tv_shows(page, per_page)
        trending_shows = get_trending_shows()

    logger.info(f"Total pages: {total_pages}")
    logger.info(f"TV Shows retrieved (for template): {tv_shows}")  # CRITICAL LOG

    # --- TEMPORARY DIAGNOSTIC RETURN ---
    return f"<pre>{tv_shows!r}</pre>"
    # --- Original return (comment out for now) ---
    # return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query, trending_shows=trending_shows)


@app.route('/show/<int:message_id>')
def show_details(message_id):
    """Displays details for a single TV show and increments its click count."""
    show = get_tv_show_by_message_id(message_id)
    if show:
        with app.app_context():  # Use application context for db access
            show.clicks += 1
            db.session.commit()
        return render_template('show_details.html', show=show)
    return "Show not found", 404

@app.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    """Redirects to the download link for a TV show."""
    show = get_tv_show_by_message_id(message_id)
    if show and show.download_link:
        return redirect(show.download_link)
    return "Show or link not found", 404

@app.route('/shows')
def list_shows():
    """Displays a list of all available TV show names."""
    show_names = get_all_show_names()
    return render_template('shows.html', show_names=show_names)

if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))  # Turn off debug for production!
