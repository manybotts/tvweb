# tv_app/app.py
import os
from flask import Flask, render_template, redirect, url_for, request, jsonify
from .tasks import update_tv_shows, test_task
from .models import db, TVShow, Genre  # Import Genre
from sqlalchemy import desc, func, and_, text  # Import 'text'
from dotenv import load_dotenv
import logging
from datetime import datetime

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///tv_shows.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_trending_shows(limit=6):
    """Retrieves the top 'limit' shows ordered by clicks."""
    return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

@app.route('/')
def index():
    search_query = request.args.get('search', '')
    search_query = search_query.strip()  # <--- Keep this!
    page = request.args.get('page', 1, type=int)
    per_page = 10

    if search_query:
        # 1. Primary Search: pg_trgm
        shows = TVShow.query.filter(
            text("show_name % :search_query")
        ).params(
            search_query=search_query
        ).order_by(
            text(f"similarity(show_name, '{search_query}') DESC")
        ).paginate(page=page, per_page=per_page, error_out=False)

        # 2. Fallback Search: ilike (if pg_trgm finds nothing)
        if not shows.items:
            shows = TVShow.query.filter(
                TVShow.show_name.ilike(f'%{search_query}%')
            ).paginate(page=page, per_page=per_page, error_out=False)

            if not shows.items:
                shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
                message = f"No close matches found for '{search_query}'. Here are all available shows!"
                return render_template('index.html', shows=shows, search_query=search_query, trending_shows=[], message=message)
        trending_shows = [] # Keep this.

    else:
        # No search query: Show recently added shows and trending
        shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        trending_shows = get_trending_shows()

    return render_template('index.html', shows=shows, search_query=search_query, trending_shows=trending_shows)
# --- (Rest of your app.py routes remain the same) ---
@app.route('/show/<int:show_id>')
def show_details(show_id):
    show = TVShow.query.get_or_404(show_id)
    show.clicks += 1
    db.session.commit()
    return render_template('show_details.html', show=show)


@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    show = TVShow.query.get_or_404(show_id)
    if show.download_link:
        return redirect(show.download_link)
    return "Show or link not found", 404

@app.route('/shows')
def list_shows():
    page = request.args.get('page', 1, type=int)
    per_page = 30
    genre_filter = request.args.get('genre')
    rating_filter = request.args.get('rating', type=float)  # Keep type conversion
    year_filter = request.args.get('year', type=int)
    sort_by = request.args.get('sort_by', 'name_asc')  # Default sort

    # Start with a base query
    query = TVShow.query

    # --- Filtering ---
    if genre_filter:
        query = query.join(TVShow.genres).filter(Genre.name == genre_filter)  # Join and filter
    if rating_filter:
        query = query.filter(TVShow.rating >= rating_filter) # Keep comparison
    if year_filter:
        query = query.filter(TVShow.year == year_filter)

    # --- Sorting ---
    if sort_by == 'name_asc':
        query = query.order_by(TVShow.show_name.asc())
    elif sort_by == 'name_desc':
        query = query.order_by(TVShow.show_name.desc())
    elif sort_by == 'date_asc':
        query = query.order_by(TVShow.created_at.asc())
    elif sort_by == 'date_desc':
        query = query.order_by(TVShow.created_at.desc())
    elif sort_by == 'rating_asc':
        query = query.order_by(TVShow.rating.asc())
    elif sort_by == 'rating_desc':
        query = query.order_by(TVShow.rating.desc())

    # --- Pagination ---
    shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    # --- Get All Genres for Dropdown ---
    all_genres = Genre.query.order_by(Genre.name).all() # Get all genres

     # --- Dynamic Year Range ---
    current_year = datetime.now().year
    min_year_result = db.session.query(func.min(TVShow.year)).scalar()  # Get min year
    min_year = min_year_result if min_year_result is not None else 1900  # Default to 1900

    # Create a list of years for the dropdown
    years = list(range(current_year, min_year - 1, -1))  # Descending order

    return render_template('shows.html', shows=shows_paginated, genres=all_genres, years=years, selected_year=year_filter, selected_rating=rating_filter)

@app.route('/update', methods=['POST'])
def update():
    update_tv_shows.delay()  # Run the task asynchronously
    return jsonify({'message': 'Update initiated'}), 202

@app.route('/test_celery')
def test_celery():
    result = test_task.delay()
    return f"Celery test task initiated. Check logs/flower. Task ID: {result.id}", 200

@app.route('/delete_all', methods=['POST'])
def delete_all_shows():
    try:
        num_rows_deleted = db.session.query(TVShow).delete()
        db.session.commit()
        return jsonify({'message': f'All {num_rows_deleted} shows deleted.'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Error deleting shows: {str(e)}'}), 500

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500
