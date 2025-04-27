# tv_app/app.py
import os
from flask import Flask, render_template, redirect, url_for, request, jsonify
# Assuming tasks are handled via Celery API as discussed
from .models import db, TVShow, Genre
from sqlalchemy import desc, func, and_, text, distinct
from dotenv import load_dotenv
import logging
from datetime import datetime # Ensure datetime is imported

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///tv_shows.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Context processor to make 'now' available to all templates (for footer year)
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow}

def get_trending_shows(limit=6):
    """Retrieves the top 'limit' shows ordered by clicks."""
    with app.app_context():
        return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

@app.route('/')
def index():
    search_query = request.args.get('search', '')
    search_query = search_query.strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10
    trending_shows = get_trending_shows()
    message = None

    if search_query:
        try:
            similarity_threshold = 0.1
            shows = TVShow.query.filter(
                func.similarity(TVShow.show_name, search_query) > similarity_threshold
            ).order_by(
                func.similarity(TVShow.show_name, search_query).desc()
            ).paginate(page=page, per_page=per_page, error_out=False)

            if not shows.items:
                shows = TVShow.query.filter(
                    TVShow.show_name.ilike(f'%{search_query}%')
                ).order_by(TVShow.created_at.desc()
                ).paginate(page=page, per_page=per_page, error_out=False)

                if not shows.items:
                    shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
                    message = f"No matches found for '{search_query}'. Showing most recent additions."
                    # Pass specific title for no results
                    return render_template('index.html', shows=shows, search_query=search_query, trending_shows=[], message=message, title=f"No Results for '{search_query}'")

        except Exception as e:
            logger.error(f"Database error during search: {e}")
            shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            message = "An error occurred during search. Please try again later."
            return render_template('index.html', shows=shows, search_query=search_query, trending_shows=trending_shows, message=message, title="Search Error")
    else:
        shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    # Pass title based on whether it's search results or homepage view
    page_title = f"Search Results: {search_query}" if search_query else "Search & Download Latest TV Shows" # Default homepage title from index.html block
    return render_template('index.html', shows=shows, search_query=search_query, trending_shows=trending_shows, message=message, title=page_title)


# ==============================================================
# UPDATED show_details ROUTE BELOW (Adds dynamic title/description)
# ==============================================================
@app.route('/show/<int:show_id>')
def show_details(show_id):
    try:
        show = TVShow.query.get_or_404(show_id)
        # It's better to increment clicks after confirming the page renders successfully,
        # perhaps using a separate tracking mechanism or ensuring the commit happens last.
        # For simplicity here, we commit after potential modification.
        show.clicks = (show.clicks or 0) + 1
        db.session.commit()

        # --- Generate Dynamic Title & Meta Description ---
        title_parts = [show.show_name]
        if show.episode_title:
            title_parts.append(show.episode_title)
        title_parts.append("Details & Download")
        page_title = " - ".join(title_parts) # Example: "Show Name - Ep Title - Details & Download"

        if show.overview:
            # Use first ~155 chars of overview for description, add call to action
            meta_desc_content = show.overview[:155] + "..." if len(show.overview) > 155 else show.overview
            meta_desc = f"{meta_desc_content} Find details and download link on iBOX TV."
        else:
            # Fallback description
            meta_desc = f"View details and download {show.show_name}{' - ' + show.episode_title if show.episode_title else ''} on iBOX TV."
        # Ensure description length is reasonable (max ~160)
        meta_desc = meta_desc[:160]

        return render_template('show_details.html',
                               show=show,
                               title=page_title,            # Pass dynamic title
                               meta_description=meta_desc)  # Pass dynamic description
    except Exception as e:
        db.session.rollback() # Ensure rollback on error
        logger.error(f"Error fetching or updating show details for ID {show_id}: {e}")
        # Pass generic error title/description
        return render_template('500.html', title="Server Error", meta_description="An error occurred viewing show details."), 500
# ==============================================================
# END OF UPDATED show_details ROUTE
# ==============================================================


@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    try:
        show = TVShow.query.get_or_404(show_id)
        if show.download_link:
            # Optionally track download clicks separately if desired
            return redirect(show.download_link)
        else:
            return "Download link not found for this show.", 404
    except Exception as e:
        logger.error(f"Error redirecting for show ID {show_id}: {e}")
        return render_template('500.html', title="Server Error", meta_description="An error occurred during redirect."), 500


# ==============================================================
# UPDATED list_shows ROUTE BELOW (Static ratings, range filter)
# ==============================================================
@app.route('/shows')
def list_shows():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 30
        genre_filter = request.args.get('genre')
        # --- Read rating filter as Integer ---
        rating_filter = request.args.get('rating', type=int)
        year_filter = request.args.get('year', type=int)
        sort_by = request.args.get('sort_by', 'name_asc')

        query = TVShow.query

        # --- Filtering ---
        if genre_filter:
            query = query.join(TVShow.genres).filter(Genre.name == genre_filter)
        if year_filter:
            query = query.filter(TVShow.year == year_filter)

        # --- Apply RATING RANGE Filter ---
        if rating_filter is not None:
            lower_bound = float(rating_filter)
            if rating_filter == 10: # Special case for 10 (>= 10.0)
                query = query.filter(TVShow.rating >= lower_bound)
            else: # For 0-9, filter range [X.0, (X+1).0)
                upper_bound = lower_bound + 1.0
                query = query.filter(
                    TVShow.rating >= lower_bound,
                    TVShow.rating < upper_bound
                )
        # --- End of RATING RANGE Filter ---

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
            query = query.order_by(TVShow.rating.asc().nullslast())
        elif sort_by == 'rating_desc':
            query = query.order_by(TVShow.rating.desc().nullslast())

        shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        # --- Get Filter Options ---
        all_genres = Genre.query.order_by(Genre.name).all()

        current_year = datetime.now().year
        min_year_result = db.session.query(func.min(TVShow.year)).filter(TVShow.year.isnot(None)).scalar()
        min_year = min_year_result if min_year_result is not None else current_year - 20
        years = list(range(current_year, min_year - 1, -1))

        # --- Use Static List for Rating Options ---
        possible_ratings = list(range(10, -1, -1)) # Static list [10, 9, ..., 0]

        # --- Render Template ---
        # Get title from template block, or set a default if needed
        page_title = "Available TV Shows" # Default title for this page
        return render_template(
            'shows.html',
            shows=shows_paginated,
            genres=all_genres,
            ratings=possible_ratings,
            years=years,
            selected_genre=genre_filter,
            selected_rating=rating_filter,
            selected_year=year_filter,
            current_sort_by=sort_by,
            title=page_title # Pass title to template context if needed by base
        )

    except Exception as e:
        logger.error(f"Error in list_shows route: {e}")
        db.session.rollback()
        return render_template('500.html', title="Server Error", meta_description="An error occurred viewing shows list."), 500
# ==============================================================
# END OF UPDATED list_shows ROUTE
# ==============================================================


# ==============================================================
# NEW privacy_policy ROUTE BELOW
# ==============================================================
@app.route('/privacy-policy')
def privacy_policy():
    # Pass title to template context if needed by base
    page_title = "Privacy Policy"
    return render_template('privacy_policy.html', title=page_title)
# ==============================================================
# END OF NEW privacy_policy ROUTE
# ==============================================================


@app.route('/update', methods=['POST'])
def update():
    try:
        # Ensure Celery task import/discovery works in your setup
        from .tasks import update_tv_shows
        update_tv_shows.delay()
        return jsonify({'message': 'Update initiated'}), 202
    except Exception as e:
        logger.error(f"Failed to initiate update task: {e}")
        return jsonify({'message': 'Error initiating update'}), 500


@app.route('/test_celery')
def test_celery():
    try:
        # Ensure Celery task import/discovery works in your setup
        from .tasks import test_task
        result = test_task.delay()
        return f"Celery test task initiated. Check logs/worker. Task ID: {result.id}", 200
    except Exception as e:
        logger.error(f"Failed to initiate test task: {e}")
        return jsonify({'message': 'Error initiating test task'}), 500

@app.route('/delete_all', methods=['POST'])
def delete_all_shows():
    # Add proper security/authentication to this route!
    try:
        num_rows_deleted = db.session.query(TVShow).delete()
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
    # Pass title/description for consistent SEO handling
    return render_template('404.html', title="Page Not Found", meta_description="The page you were looking for could not be found."), 404

@app.errorhandler(500)
def internal_server_error(e):
    try:
        db.session.rollback()
    except Exception as rollback_error:
        logger.error(f"Error during rollback in 500 handler: {rollback_error}")
    # Pass title/description for consistent SEO handling
    return render_template('500.html', title="Internal Server Error", meta_description="We encountered an internal error. Please try again later."), 500

# Optional: Add main execution block for local development if needed
# if __name__ == '__main__':
#     port = int(os.environ.get('PORT', 5000))
#     app.run(debug=True, host='0.0.0.0', port=port) # Use debug=False in production
