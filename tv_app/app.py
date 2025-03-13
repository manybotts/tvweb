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
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# --- Database Operations (No changes here) ---

def get_all_tv_shows(page=1, per_page=10, search_query=None):
    """Retrieves TV shows with pagination and search."""
    logger.info(f"get_all_tv_shows called: page={page}, per_page={per_page}, search_query={search_query!r}")
    offset = (page - 1) * per_page
    query = TVShow.query

    if search_query:
        logger.info(f"Applying search filter: {search_query!r}")
        query = query.filter(TVShow.show_name.ilike(f"%{search_query}%"))

    total_shows = query.count()
    logger.info(f"Total shows matching query: {total_shows}")

    tv_shows = query.order_by(TVShow.created_at.desc()).offset(offset).limit(per_page).all()
    logger.info(f"Retrieved shows (raw data): {tv_shows}")

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
    """Homepage: Trigger task and display shows (for debugging)."""
    # --- TEMPORARY TASK EXECUTION (FOR DEBUGGING ONLY) ---
    logger.info("Manually calling update_tv_shows task (synchronously)...")
    try:
        update_tv_shows()  # Call the task DIRECTLY (no .delay())
        logger.info("update_tv_shows task completed successfully (synchronously).")
    except Exception as e:
        logger.exception("Error during synchronous update_tv_shows execution:")
    # --- END TEMPORARY TASK EXECUTION ---


    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    # logger.info("About to enqueue update_tv_shows task") # Removed the .delay()
    # update_tv_shows.delay()  # Enqueue the Celery task.
    # logger.info("update_tv_shows task enqueued")


    if search_query:
        tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
        trending_shows = []
    else:
        tv_shows, total_pages = get_all_tv_shows(page, per_page)
        trending_shows = get_trending_shows()

    logger.info(f"Total pages: {total_pages}")
    logger.info(f"TV Shows retrieved (for template): {tv_shows}")

    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query, trending_shows=trending_shows)



@app.route('/show/<int:message_id>')
def show_details(message_id):
    """Displays details for a single TV show."""
    show = get_tv_show_by_message_id(message_id)
    if show:
        with app.app_context():
            show.clicks += 1
            db.session.commit()
        return render_template('show_details.html', show=show)
    return "Show not found", 404

@app.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    """Redirects to the download link."""
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
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
