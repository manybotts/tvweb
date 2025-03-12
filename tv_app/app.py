import os
import re
from flask import Flask, render_template, redirect, url_for, request
import logging
from dotenv import load_dotenv
#from .tasks import update_tv_shows #Removed since celery beat runs the update
from .models import db, TVShow
from sqlalchemy import desc

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# --- Database Operations ---

def get_all_tv_shows(page=1, per_page=10, search_query=None):
    """Retrieves TV shows with pagination and search."""
    offset = (page - 1) * per_page
    query = TVShow.query

    if search_query:
        query = query.filter(TVShow.show_name.ilike(f"%{search_query}%"))

    total_shows = query.count()
    tv_shows = query.order_by(TVShow.created_at.desc()).offset(offset).limit(per_page).all()
    total_pages = (total_shows + per_page - 1) // per_page

    return tv_shows, total_pages

def get_tv_show_by_message_id(message_id):
    """Retrieves a single TV show by its message_id."""
    return TVShow.query.filter_by(message_id=message_id).first()

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

    if search_query:
        # If there's a search query, *only* fetch the matching shows.
        tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
        trending_shows = []  # Don't fetch trending shows if searching
    else:
        # If there's *no* search query, fetch both all shows and trending shows.
        tv_shows, total_pages = get_all_tv_shows(page, per_page)
        trending_shows = get_trending_shows()

    logger.info(f"Total pages: {total_pages}")
    logger.info(f"TV Shows retrieved: {tv_shows}")

    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query, trending_shows=trending_shows)


@app.route('/show/<int:message_id>')  # Keep as int:message_id
def show_details(message_id):
    """Displays details for a single TV show and increments its click count."""
    # Convert message_id to int explicitly, if it is coming as a string.
    message_id = int(message_id)
    show = get_tv_show_by_message_id(message_id)

    if show:
        with app.app_context():
          show.clicks += 1
          db.session.commit()
        return render_template('show_details.html', show=show)
    return "Show not found", 404

@app.route('/redirect/<int:message_id>')  # Keep as int:message_id
def redirect_to_download(message_id):
    """Redirects to the download link for a TV show."""
    # Convert message_id to int explicitly, if it is coming as a string.
    message_id = int(message_id)
    show = get_tv_show_by_message_id(message_id)
    if show:  # Check if show exists
        if show.download_link:
            return redirect(show.download_link)
        else:
            return "Download link not found", 404  # Handle missing link
    else:
        return "Show not found", 404  # Handle missing show
