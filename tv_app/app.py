# tv_app/app.py
import os
from flask import Flask, render_template, redirect, url_for, request, jsonify
# Note: Assuming tasks are called via Celery API and not directly imported here
# based on your description to avoid circular imports.
# If you ARE importing them, ensure it works with your setup.
# from .tasks import update_tv_shows, test_task
from .models import db, TVShow, Genre  # Import Genre - Correct relative import
from sqlalchemy import desc, func, and_, text, distinct  # Import 'distinct' and 'text'
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
    # Ensure the query is executed within the application context if needed,
    # though usually direct Flask routes have context.
    with app.app_context():
        return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

@app.route('/')
def index():
    search_query = request.args.get('search', '')
    search_query = search_query.strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10
    trending_shows = get_trending_shows() #Always call this

    # Make sure you have enabled the pg_trgm extension in your PostgreSQL DB
    # Example: Connect to psql and run `CREATE EXTENSION IF NOT EXISTS pg_trgm;`

    if search_query:
        # Use try-except block for database operations, especially those relying on extensions
        try:
            # 1. Primary Search: pg_trgm with Similarity Threshold
            # Ensure the threshold is set appropriately, e.g., using `set_limit` or direct comparison
            # The following assumes similarity > 0.1 (adjust as needed)
            # Also ensure the text() construct parameters are passed correctly.
            similarity_threshold = 0.1 # Example threshold
            shows = TVShow.query.filter(
                func.similarity(TVShow.show_name, search_query) > similarity_threshold
            ).order_by(
                func.similarity(TVShow.show_name, search_query).desc()
            ).paginate(page=page, per_page=per_page, error_out=False)

            # 2. Fallback Search: ilike (if pg_trgm finds nothing or similarity is too low)
            if not shows.items:
                shows = TVShow.query.filter(
                    TVShow.show_name.ilike(f'%{search_query}%')
                ).order_by(TVShow.created_at.desc() # Add sensible order for fallback
                ).paginate(page=page, per_page=per_page, error_out=False)

                if not shows.items:
                    # No results from either search, show message and recent shows
                    shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
                    message = f"No matches found for '{search_query}'. Showing most recent additions."
                    # Pass empty trending shows if search yields nothing specific
                    return render_template('index.html', shows=shows, search_query=search_query, trending_shows=[], message=message)

        except Exception as e:
            logger.error(f"Database error during search: {e}")
            # Handle error gracefully, maybe show an error message or default list
            shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            message = "An error occurred during search. Please try again later."
            return render_template('index.html', shows=shows, search_query=search_query, trending_shows=trending_shows, message=message)

    else:
        # No search query: Show recently added shows and trending
        shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        message = None # No message needed when just Browse

    return render_template('index.html', shows=shows, search_query=search_query, trending_shows=trending_shows, message=message)


@app.route('/show/<int:show_id>')
def show_details(show_id):
    # Use try-except for database operations
    try:
        show = TVShow.query.get_or_404(show_id)
        # Increment clicks only if the show is found
        show.clicks = (show.clicks or 0) + 1 # Handle potential None for clicks
        db.session.commit()
        return render_template('show_details.html', show=show)
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error fetching or updating show details for ID {show_id}: {e}")
        return render_template('500.html'), 500 # Or a more specific error page/message

@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    # Use try-except for database operations
    try:
        show = TVShow.query.get_or_404(show_id)
        if show.download_link:
            # Consider incrementing clicks here too if preferred
            # show.clicks = (show.clicks or 0) + 1
            # db.session.commit()
            return redirect(show.download_link)
        else:
            return "Download link not found for this show.", 404
    except Exception as e:
        db.session.rollback() # Rollback if commit was intended above
        logger.error(f"Error redirecting for show ID {show_id}: {e}")
        return render_template('500.html'), 500

@app.route('/shows')
def list_shows():
    try: # Wrap route logic in try-except
        page = request.args.get('page', 1, type=int)
        per_page = 30 # Consider making this configurable
        genre_filter = request.args.get('genre')
        # Use float or int based on your rating data type
        rating_filter = request.args.get('rating', type=float) # Or type=int
        year_filter = request.args.get('year', type=int)
        sort_by = request.args.get('sort_by', 'name_asc')

        # Start with a base query
        query = TVShow.query

        # --- Filtering ---
        if genre_filter:
            # Ensure Genre model is joined correctly if filtering by genre name
            query = query.join(TVShow.genres).filter(Genre.name == genre_filter)
        if rating_filter is not None: # Check against None specifically
             # Ensure comparison works with your data type (float/int)
            query = query.filter(TVShow.rating >= rating_filter)
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
            # Add nullslast() or nullsfirst() if rating can be NULL
            query = query.order_by(TVShow.rating.asc().nullslast())
        elif sort_by == 'rating_desc':
            query = query.order_by(TVShow.rating.desc().nullslast())
        # Add a default sort if none of the above match?
        # else:
        #    query = query.order_by(TVShow.show_name.asc()) # Example default


        # --- Pagination ---
        shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        # --- Get Filter Options ---
        # Genres
        all_genres = Genre.query.order_by(Genre.name).all()

        # Years (Dynamic Range)
        current_year = datetime.now().year
        min_year_result = db.session.query(func.min(TVShow.year)).filter(TVShow.year.isnot(None)).scalar()
        min_year = min_year_result if min_year_result is not None else current_year - 20 # Sensible default
        years = list(range(current_year, min_year - 1, -1))

        # --- Get Possible Ratings --- ### THIS IS THE FIX ###
        # Query distinct ratings, filter None, order descending.
        rating_results = db.session.query(distinct(TVShow.rating))\
                                   .filter(TVShow.rating.isnot(None))\
                                   .order_by(TVShow.rating.desc())\
                                   .all()
        # Extract float/int value - ensure type consistency with filter/model
        possible_ratings = [r[0] for r in rating_results]
        # Example: Convert to int if ratings are stored/filtered as integers
        # possible_ratings = [int(r[0]) for r in rating_results if r[0] is not None]


        # --- Render Template ---
        return render_template(
            'shows.html',
            shows=shows_paginated,
            genres=all_genres,
            ratings=possible_ratings,  # <-- Pass the generated list here
            years=years,
            # Pass selected values back to template to keep filters selected
            selected_genre=genre_filter,
            selected_rating=rating_filter,
            selected_year=year_filter,
            current_sort_by=sort_by
        )

    except Exception as e:
        logger.error(f"Error in list_shows route: {e}")
        db.session.rollback() # Rollback in case of DB issues
        return render_template('500.html'), 500 # Render generic error page


@app.route('/update', methods=['POST'])
def update():
    # Assuming update_tv_shows is correctly set up in Celery
    # and accessible via Celery's task discovery/API
    try:
        # You need to ensure Celery app is configured and tasks are registered
        # This call assumes Celery is set up correctly elsewhere
        from .tasks import update_tv_shows # Local import if needed, or rely on Celery app
        update_tv_shows.delay()
        return jsonify({'message': 'Update initiated'}), 202
    except Exception as e:
        logger.error(f"Failed to initiate update task: {e}")
        return jsonify({'message': 'Error initiating update'}), 500


@app.route('/test_celery')
def test_celery():
    try:
        from .tasks import test_task # Local import if needed, or rely on Celery app
        result = test_task.delay()
        return f"Celery test task initiated. Check logs/worker. Task ID: {result.id}", 200
    except Exception as e:
        logger.error(f"Failed to initiate test task: {e}")
        return jsonify({'message': 'Error initiating test task'}), 500

@app.route('/delete_all', methods=['POST'])
# Add protection to this route (e.g., admin login, specific IP, secret key)
def delete_all_shows():
    # Example: Basic secret key check - VERY INSECURE, use proper auth
    # if request.headers.get('X-Admin-Secret') != os.environ.get('ADMIN_SECRET'):
    #     return jsonify({'message': 'Unauthorized'}), 403

    try:
        num_rows_deleted = db.session.query(TVShow).delete()
        # Also delete related genre associations if necessary, or handle cascade
        db.session.commit()
        logger.info(f'All {num_rows_deleted} shows deleted.')
        return jsonify({'message': f'All {num_rows_deleted} shows deleted.'}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error deleting all shows: {e}')
        return jsonify({'message': f'Error deleting shows: {str(e)}'}), 500


# --- Error Handlers ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    # It's good practice to rollback the session in case of 500 errors
    # if the error originated from a DB operation that wasn't caught earlier
    try:
        db.session.rollback()
    except Exception as rollback_error:
        logger.error(f"Error during rollback in 500 handler: {rollback_error}")
    return render_template('500.html'), 500

# --- Main Execution (for local development) ---
# This block allows running `python tv_app/app.py` directly for testing
# Ensure db.create_all() is handled appropriately (e.g., via init_db or migrations)
# if __name__ == '__main__':
#     # Make sure tables are created if they don't exist when running directly
#     # with app.app_context():
#     #     db.create_all() # Use migrations (Flask-Migrate) in production instead
#     port = int(os.environ.get('PORT', 5000))
#     app.run(debug=True, host='0.0.0.0', port=port) # debug=True is NOT for production

