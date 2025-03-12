import os
import re
from flask import Flask, render_template, redirect, url_for, request
import logging
from dotenv import load_dotenv
from tasks import update_tv_shows, test_task
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')  # Use the DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Suppress a warning
db = SQLAlchemy(app)


# --- Database Model (SQLAlchemy) ---
class TVShow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    show_name = db.Column(db.String(255), unique=True, nullable=False)
    season_episode = db.Column(db.String(255))
    download_link = db.Column(db.String(255))
    message_id = db.Column(db.Integer, unique=True) # Keep message_id, useful for linking
    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=db.func.now()) # Use server default

    def __repr__(self):
        return f'<TVShow {self.show_name}>'


# --- Database Operations ---
# Removed the old get_db and related functions.

with app.app_context():
    db.create_all()
    logger.info("SQLAlchemy and PostgreSQL Database connected")


def get_all_tv_shows(page=1, per_page=9, search_query=None):
    """Retrieves TV shows with pagination and search."""
    offset = (page - 1) * per_page
    query = TVShow.query

    if search_query:
        query = query.filter(TVShow.show_name.ilike(f"%{search_query}%"))  # Case-insensitive search

    total_shows = query.count()
    tv_shows = query.order_by(desc(TVShow.created_at)).offset(offset).limit(per_page).all()
    total_pages = (total_shows + per_page - 1) // per_page

    return tv_shows, total_pages

def get_tv_show_by_message_id(message_id):
    """Retrieves a single TV show by its message_id."""
    return TVShow.query.filter_by(message_id=message_id).first()

def get_all_show_names():
    """Retrieves a list of all unique show names."""
    return [show.show_name for show in TVShow.query.distinct(TVShow.show_name).all()]

# --- Routes ---

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 9

    logger.info("About to enqueue update_tv_shows task")
    update_tv_shows.delay()  # Correctly enqueue the task
    logger.info("update_tv_shows task enqueued")

    tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)

    logger.info(f"Total pages: {total_pages}")  # Keep this for debugging
    logger.info(f"TV Shows retrieved: {tv_shows}") # Keep this for debugging

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
