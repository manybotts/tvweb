import os
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text
from .models import db, TVShow
from .tasks import update_tv_shows, normalize_string  # Import normalize_string
import logging
from thefuzz import process

app = Flask(__name__)

# Database configuration (using environment variables)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Configure logging (Good practice)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def get_trending_shows(limit=5):
    """Retrieves the top 'limit' trending shows, ordered by clicks."""
    return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()


# --- Routes ---

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Number of shows per page

    # 1. **Alphabetical Grouping:**
    shows = TVShow.query.filter(TVShow.show_name.isnot(None)).order_by(TVShow.show_name).all()
    grouped_shows = {}
    for show in shows:
        first_letter = show.show_name[0].upper()
        if first_letter not in grouped_shows:
            grouped_shows[first_letter] = []
        grouped_shows[first_letter].append(show)

    # 2. **Trending Shows:** (Only on the main page, not during search)
    trending_shows = get_trending_shows()

    # 3. **Pagination:** (Applied to the grouped display)
    # Manually paginate the grouped_shows dictionary
    paginated_groups = {}
    group_keys = sorted(grouped_shows.keys())  # Get sorted keys (letters)
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

    # Create a custom pagination object.
    total_shows = sum(len(shows) for shows in grouped_shows.values())
    pagination = CustomPagination(page, per_page, total_shows, [])  # Empty items list
    pagination.grouped_items = paginated_groups # Pass grouped items instead

    return render_template('index.html', grouped_shows=paginated_groups, trending_shows=trending_shows, pagination=pagination)


@app.route('/search')
def search():
    query = request.args.get('query', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Shows per page, even in search results

    if query:
        normalized_query = normalize_string(query)

        # --- FULL-TEXT SEARCH (PostgreSQL) ---
        sql = text("""
            SELECT * FROM tv_show
            WHERE search_vector @@ to_tsquery(:query)
            ORDER BY ts_rank(search_vector, to_tsquery(:query)) DESC;
        """)
        result = db.session.execute(sql, {"query": f"{normalized_query}:*"})
        shows = result.fetchall()
        shows = [TVShow(**dict(row)) for row in shows]  # Convert to TVShow objects


        # --- FUZZY SEARCH (for "Related Shows") ---
        #  (Only if full-text search returns *few* or no results)
        if len(shows) < per_page:
            all_show_names = [show.show_name for show in TVShow.query.all() if show.show_name]
            fuzzy_matches = process.extract(normalized_query, all_show_names, limit=per_page * 2)
            related_shows = []
            for show_name, score in fuzzy_matches:
                if score >= 60:  #  Score threshold
                    show = TVShow.query.filter_by(show_name=show_name).first()
                    if show and show not in shows: # Avoid duplicates
                        related_shows.append(show)

            # Combine, prioritizing full-text search results.
            shows.extend(related_shows)

        # --- PAGINATION (after combining results) ---
        pagination = CustomPagination(page, per_page, len(shows), shows[ (page-1)*per_page : page*per_page ])

    else:
        # If no search query, redirect to the main page.
        return redirect(url_for('index'))

    return render_template('search_results.html', shows=pagination.items, query=query, pagination=pagination)


@app.route('/show/<int:show_id>')
def show_detail(show_id):
    show = TVShow.query.get_or_404(show_id)
    # Increment clicks (for trending)
    show.clicks += 1
    db.session.commit()

    episodes = TVShow.query.filter(TVShow.show_id == show.id).order_by(TVShow.season_range, TVShow.episode_number).all()
    seasons = {}  # Organize episodes by season
    for episode in episodes:
        if episode.season_range not in seasons:
            seasons[episode.season_range] = []
        seasons[episode.season_range].append(episode)

    return render_template('show_detail.html', show=show, seasons=seasons)


@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    show = TVShow.query.get_or_404(show_id)
    if show.download_link:  # Check if a download link exists
        return redirect(show.download_link)
    return "Download link not found", 404  # Or render a "not found" template


@app.route('/shows')
def list_shows():
    """Displays a list of all available TV show *names*, with pagination."""
    page = request.args.get('page', 1, type=int)
    per_page = 30  # Number of show *names* per page

    # Use paginate on the query that retrieves distinct show names.
    shows_paginated = TVShow.query.distinct(TVShow.show_name).order_by(TVShow.show_name).paginate(page=page, per_page=per_page, error_out=False)

    # Extract show *names* from the paginated result (for efficiency).
    show_names = [show.show_name for show in shows_paginated.items]

    return render_template('shows.html', show_names=show_names, shows=shows_paginated) # Pass pagination object

@app.route('/update_database', methods=['POST'])
def update_database():
    update_tv_shows.delay()  # Correctly triggers Celery task
    return jsonify({'message': 'Database update initiated'}), 202

@app.route('/delete_all', methods=['POST'])
def delete_all_shows():
    try:
        num_rows_deleted = db.session.query(TVShow).delete()
        db.session.commit()
        return jsonify({'message': f'All {num_rows_deleted} shows deleted.'}), 200
    except Exception as e:
        db.session.rollback()
        logger.exception("Error deleting shows:") #Log the exception
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
