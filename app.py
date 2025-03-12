import os
from flask import Flask, render_template, redirect, url_for, request
import logging
from dotenv import load_dotenv
from tasks import update_tv_shows
from models import db, TVShow

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')

# --- CORRECT DATABASE CONFIGURATION (Simplified) ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

print("DATABASE_URL:", os.environ.get('DATABASE_URL'))  # DIAGNOSTIC PRINT

db.init_app(app)

with app.app_context():
    db.create_all()
    logger.info("SQLAlchemy and PostgreSQL Database connected")

# ... (Rest of your app.py - routes, functions, etc. - NO CHANGES HERE) ...
@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 9

    logger.info("About to enqueue update_tv_shows task")
    update_tv_shows.delay()  # Enqueue the Celery task
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
    if show and show.download_link:
        return redirect(show.download_link)
    return "Show or link not found", 404

@app.route('/shows')
def list_shows():
    """Displays a list of all available TV show names."""
    show_names = get_all_show_names()
    return render_template('shows.html', show_names=show_names)
def get_all_tv_shows(page=1, per_page=9, search_query=None):
    """Retrieves TV shows with pagination and search."""
    offset = (page - 1) * per_page
    query = TVShow.query

    if search_query:
        query = query.filter(TVShow.show_name.ilike(f"%{search_query}%"))  # Case-insensitive search

    total_shows = query.count()
    tv_shows = query.order_by(TVShow.created_at.desc()).offset(offset).limit(per_page).all()
    total_pages = (total_shows + per_page - 1) // per_page

    return tv_shows, total_pages

def get_tv_show_by_message_id(message_id):
    """Retrieves a single TV show by its message_id."""
    return TVShow.query.filter_by(message_id=message_id).first()

def get_all_show_names():
    """Retrieves a list of all unique show names."""
    return [show.show_name for show in TVShow.query.distinct(TVShow.show_name).all()]

if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
