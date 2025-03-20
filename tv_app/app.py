# tv_app/app.py
import os
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text  # Import 'text'
from .models import db, TVShow  # Import db and TVShow from models
from .tasks import update_tv_shows, normalize_string  # Import normalize_string
import logging
from thefuzz import process, fuzz

app = Flask(__name__)

# Database configuration (using environment variables)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)  # Initialize db with the app

# Configure logging (Good practice)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def get_trending_shows(limit=5):
    """Retrieves the top 'limit' trending shows, ordered by clicks."""
    return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

# --- Pagination ---
def paginate_results(results, page, per_page):
    """Paginates a list of results manually."""
    from flask_sqlalchemy import pagination

    start = (page - 1) * per_page
    end = start + per_page
    paginated_items = results[start:end]

    # Create a CustomPagination object
    pagination_obj = CustomPagination(page, per_page, len(results), paginated_items)
    return pagination_obj

class CustomPagination:
    """
    A custom pagination class to mimic Flask-SQLAlchemy's Pagination object
    for use with pre-fetched lists of results.
    """
    def __init__(self, page, per_page, total, items):
        self.page = page
        self.per_page = per_page
        self.total = total
        self.items = items
        self.grouped_items = {} # For grouped display

    @property
    def pages(self):
        """Total number of pages."""
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self):
        """True if there's a previous page."""
        return self.page > 1

    @property
    def has_next(self):
        """True if there's a next page."""
        return self.page < self.pages

    def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
        """Iterates over page numbers for pagination controls."""
        last = 0
        for num in range(1, self.pages + 1):
            if num <= left_edge or \
               (num > self.page - left_current - 1 and num < self.page + right_current) or \
               num > self.pages - right_edge:
                if last + 1 != num:
                    yield None  # Represents a gap (...)
                yield num
                last = num


# --- Routes ---

@app.route('/')
def index():
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    message = None  # Initialize message

    if search_query:
        normalized_query = normalize_string(search_query)  # Normalize the query

        # 1. Exact Match (Highest Priority)
        exact_match = (
            TVShow.query.filter(func.lower(TVShow.show_name) == func.lower(normalized_query)).first()
        )
        # 2. Partial Match (Using SQLAlchemy's ilike)
        partial_matches = TVShow.query.filter(
            TVShow.show_name.ilike(f'%{normalized_query}%')
        ).all()

        # 3. Fuzzy Matching (for Related Results)
        all_show_names = [show.show_name for show in TVShow.query.all() if show.show_name]  # Ensure show.show_name is not None
        # NO LIMIT HERE
        fuzzy_matches = process.extract(normalized_query, all_show_names)


        # Combine and Prioritize Results
        results = []
        if exact_match:
            results.append(exact_match)
        for show in partial_matches:
            if show not in results:
                results.append(show)

        # --- ADDED SCORE THRESHOLD ---
        for show_name, score in fuzzy_matches:
            if score >= 60:  #  Only include fuzzy matches with a score of 60 or higher
                show = TVShow.query.filter_by(show_name=show_name).first()
                if show and show not in results:
                    results.append(show)

        # Paginate the combined results
        shows = paginate_results(results, page, per_page)

        # Check if any results were found
        if not shows.items:
            message = (
                f"No shows found matching '{search_query}'. Here are some similar shows:"
            )
            # If no exact or partial matches, show related (fuzzy) results
            shows = paginate_results(
                results, page, per_page
            )  # paginate all results, including fuzzy.
            if not shows.items:  # still empty after fuzzy?
                message = f"No shows found matching '{search_query}'. Displaying all shows."
                shows = (
                    TVShow.query.order_by(TVShow.created_at.desc())
                    .paginate(page=page, per_page=per_page, error_out=False)
                )

        trending_shows = [] # No trending shows if it's a search

    else:  # No search query
        # --- ALPHABETICAL GROUPING ---
        shows = TVShow.query.filter(TVShow.show_name.isnot(None)).order_by(TVShow.show_name).all()
        grouped_shows = {}
        for show in shows:
            first_letter = show.show_name[0].upper()
            if first_letter not in grouped_shows:
                grouped_shows[first_letter] = []
            grouped_shows[first_letter].append(show)

        # --- PAGINATION (applied to grouped display) ---
        paginated_groups = {}
        group_keys = sorted(grouped_shows.keys()) # Get sorted keys (letters)
        start = (page - 1) * per_page
        end = start + per_page
        current_index = 0

        for letter in group_keys:
            shows_in_group = grouped_shows[letter]
            for show in shows_in_group:
                if start <= current_index < end:
                    if letter not in paginated_groups:
                        paginated_groups[letter] = []
                    paginated_groups[letter].append(show)
                current_index += 1


        # --- TRENDING SHOWS ---
        trending_shows = get_trending_shows()
        total_shows = sum(len(shows) for shows in grouped_shows.values())
        pagination = CustomPagination(page, per_page, total_shows, [])  # Empty items list
        pagination.grouped_items = paginated_groups # Pass grouped items instead

        return render_template('index.html', grouped_shows=paginated_groups, trending_shows=trending_shows, search_query=search_query, message=message, pagination=pagination)

    return render_template('index.html', shows=pagination.items, search_query=search_query, trending_shows=trending_shows, pagination=pagination,message=message)


@app.route('/show/<int:show_id>')
def show_details(show_id):
    """Displays details for a single TV show and increments its click count."""
    show = TVShow.query.get_or_404(show_id)
    show.clicks += 1
    db.session.commit()
    episodes = TVShow.query.filter(TVShow.show_id == show.id).order_by(TVShow.season_range, TVShow.episode_number).all()

    seasons = {}
    for episode in episodes:
        if episode.season_range not in seasons:
            seasons[episode.season_range] = []
        seasons[episode.season_range].append(episode)

    return render_template('show_details.html', show=show, seasons=seasons)

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
    shows_paginated = TVShow.query.order_by(TVShow.show_name).distinct(TVShow.show_name).paginate(page=page, per_page=per_page, error_out=False)
    show_names = [show.show_name for show in shows_paginated.items]
    return render_template('shows.html', show_names=show_names, shows=shows_paginated)



@app.route('/update', methods=['POST'])
def update():
    update_tv_shows.delay()  # Run the task asynchronously
    return jsonify({'message': 'Update initiated'}), 202


@app.route('/delete_all', methods=['POST'])
def delete_all_shows():
    try:
        num_rows_deleted = db.session.query(TVShow).delete()
        db.session.commit()
        return jsonify({'message': f'All {num_rows_deleted} shows deleted.'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Error deleting shows: {str(e)}'}), 500

# --- Error Handlers ---

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# --- Custom Pagination Class ---

class CustomPagination:
    """
    A custom pagination class to mimic Flask-SQLAlchemy's Pagination object
    for use with pre-fetched lists of results.
    """
    def __init__(self, page, per_page, total, items):
        self.page = page
        self.per_page = per_page
        self.total = total
        self.items = items
        self.grouped_items = {} # For grouped display

    @property
    def pages(self):
        """Total number of pages."""
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self):
        """True if there's a previous page."""
        return self.page > 1

    @property
    def has_next(self):
        """True if there's a next page."""
        return self.page < self.pages

    def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
        """Iterates over page numbers for pagination controls."""
        last = 0
        for num in range(1, self.pages + 1):
            if num <= left_edge or \
               (num > self.page - left_current - 1 and num < self.page + right_current) or \
               num > self.pages - right_edge:
                if last + 1 != num:
                    yield None  # Represents a gap (...)
                yield num
                last = num

if __name__ == '__main__':
    app.run(debug=True)
