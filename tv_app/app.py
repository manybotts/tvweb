import os
import re
from flask import Flask, render_template, redirect, url_for, request
import logging
from dotenv import load_dotenv
from .models import db, TVShow  # Relative import!
from sqlalchemy import desc
from .tasks import make_celery  # Import make_celery

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['REDIS_URL'] = os.environ.get('REDIS_URL')  # For Celery
    db.init_app(app)

    # Create Celery instance *after* app, using make_celery
    celery = make_celery(app)


    # --- Database Operations ---

    def get_all_tv_shows(page=1, per_page=10, search_query=None):
        """Retrieves TV shows with pagination and search."""
        offset = (page - 1) * per_page
        query = TVShow.query

        if search_query:
            query = query.filter(TVShow.show_name.ilike(f"%{search_query}%"))

        total_shows = query.count()
        tv_shows = query.order_by(desc(TVShow.created_at)).offset(offset).limit(per_page).all()
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
        return TVShow.query.order_by(desc(TVShow.clicks)).limit(limit).all()

    # --- Routes ---

    @app.route('/')
    def index():
        """Homepage: displays TV shows with pagination and search."""
        search_query = request.args.get('search', '')
        page = request.args.get('page', 1, type=int)
        per_page = 10

        if search_query:
            tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
            trending_shows = []  # No trending shows when searching
        else:
            tv_shows, total_pages = get_all_tv_shows(page, per_page)
            trending_shows = get_trending_shows()

        return render_template('index.html', tv_shows=tv_shows, total_pages=total_pages,
                            current_page=page, search_query=search_query, trending_shows=trending_shows)
    @app.route('/show/<int:message_id>')
    def show_detail(message_id):
        show = get_tv_show_by_message_id(message_id)
        if show:
            show.clicks += 1
            db.session.commit()
            return render_template('show_detail.html', show=show)
        return "Show not found", 404
    @app.route('/shows_list')
    def shows_list():
      show_names = get_all_show_names()
      return render_template('shows_list.html', show_names=show_names)

    @app.route('/search_results')
    def search_results():
        search_query = request.args.get('query', '')
        page = request.args.get('page', 1, type=int)
        per_page = 10
        tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
        return render_template('search_results.html', tv_shows=tv_shows, total_pages=total_pages, current_page=page, search_query=search_query)

    @app.route('/latest_shows')
    def latest_shows():
      page = request.args.get('page', 1, type=int)
      per_page = 10  # Number of latest shows per page
      tv_shows, total_pages = get_all_tv_shows(page=page, per_page=per_page)
      return render_template('latest_shows.html', tv_shows=tv_shows, total_pages=total_pages, current_page=page)
    return app
