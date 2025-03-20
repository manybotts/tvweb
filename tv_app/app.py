# tv_app/app.py
import os
from flask import (Flask, render_template, redirect, url_for, request, jsonify,
                   flash, abort)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
from dotenv import load_dotenv
from .models import db, User, Show, Episodes  # Import all models
from .tasks import update_tv_shows, normalize_string  # Import tasks and helper functions
from sqlalchemy import desc, func
from thefuzz import process, fuzz
import datetime
from functools import wraps
from urllib.parse import urlparse, urljoin
from forms import AdminLoginForm, AddShowForm, AddEpisodeForm


load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "your_secret_key")  # ALWAYS use environment variables for secrets!
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///tv_shows.db") # Use SQLite if no DB URL is set
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False  # Disable modification tracking
db.init_app(app)

# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'  # Redirect to the login page if not authenticated

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Helper Functions ---

def get_trending_shows(limit=5):
    """Retrieves the top 'limit' trending shows, ordered by clicks."""
    return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

def is_safe_url(target):
    """Checks if the target URL is safe for redirection (prevents open redirects)."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def admin_required(f):
    """Decorator for admin-only routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('index'))  # Redirect to a safe page
        return f(*args, **kwargs)
    return decorated_function


# --- Routes ---

@app.route("/")
def index():
    """Homepage: displays TV shows with pagination, improved search, and trending shows."""
    search_query = request.args.get("search", "")
    page = request.args.get("page", 1, type=int)
    per_page = 10
    message = None  # Initialize message

    if search_query:
        normalized_query = normalize_string(search_query)  # Normalize the query

        # 1. Exact Match (Highest Priority)
        exact_match = (
            TVShow.query.filter(TVShow.show_name == normalized_query).first()
        )

        # 2. Partial Match (Using SQLAlchemy's ilike)
        partial_matches = TVShow.query.filter(
            TVShow.show_name.ilike(f"%{normalized_query}%")
        ).all()

        # 3. Fuzzy Matching (for Related Results)
        all_show_names = [show.show_name for show in TVShow.query.all()]
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

        trending_shows = []  # No trending shows if it's a search

    else:
        # No search query: display all shows, ordered by creation date
        shows = (
            TVShow.query.order_by(TVShow.created_at.desc())
            .paginate(page=page, per_page=per_page, error_out=False)
        )
        trending_shows = get_trending_shows()
        message = None

    return render_template(
        "index.html",
        shows=shows,
        search_query=search_query,
        trending_shows=trending_shows,
        message=message,
    )


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

@app.route("/show/<int:show_id>")
def show_details(show_id):
    """Displays details for a single TV show and increments its click count."""
    show = TVShow.query.get_or_404(show_id)  # Use get_or_404 with the primary key
    show.clicks += 1
    db.session.commit()
    episodes = Episodes.query.filter_by(show_id=show.id).order_by(Episodes.season_number, Episodes.episode_number).all()
    return render_template("show_details.html", show=show, episodes=episodes)


@app.route("/redirect/<int:show_id>")
def redirect_to_download(show_id):
    """Redirects to the download link for a TV show."""
    show = TVShow.query.get_or_404(show_id)  # Use get_or_404 with the primary key
    if show.download_link:
        return redirect(show.download_link)
    return "Show or link not found", 404


@app.route("/shows")
def list_shows():
    page = request.args.get("page", 1, type=int)
    per_page = 30
    sort_by = request.args.get("sort", "name")  # Default sort by name
    filter_genre = request.args.get("genre")
    filter_year = request.args.get("year")
    filter_rating = request.args.get("rating")
    now = datetime.datetime.now()  # Get current datetime

    # Start with a base query
    query = TVShow.query

    # Apply filtering. Handle 'all' option.
    if filter_genre and filter_genre != "all":
        query = query.filter(TVShow.genre.ilike(f"%{filter_genre}%"))

    if filter_year and filter_year != "all":
        try:
            filter_year = int(filter_year)
            query = query.filter(TVShow.year == filter_year)
        except ValueError:
            pass  # Ignore invalid year

    if filter_rating and filter_rating != "all":
        try:
            filter_rating = float(filter_rating)
            query = query.filter(TVShow.vote_average >= filter_rating)
        except ValueError:
            pass #Ignore invalid rating input

    # Apply sorting *before* distinct
    if sort_by == "name":
        query = query.order_by(TVShow.show_name)
    elif sort_by == "popularity":
        query = query.order_by(TVShow.clicks.desc(), TVShow.show_name)  # Sort by name within popularity
    elif sort_by == "year":
        query = query.order_by(TVShow.year.desc(), TVShow.show_name)  # Sort by name within year
    elif sort_by == "rating":
        query = query.order_by(TVShow.vote_average.desc(), TVShow.show_name)  # Sort by name within rating

    # *Now* apply distinct, after filtering and sorting, AND order by show_name FIRST
    query = query.distinct(TVShow.show_name).order_by(TVShow.show_name)

    # Paginate the query *after* filtering, sorting, and distinct
    shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get unique genres for the filter, from the *filtered* query.  Much more efficient.
    all_genres = db.session.query(TVShow.genre).distinct().all()
    all_genres = sorted({g for sublist in all_genres for g in (sublist[0] or '').split(', ') if g})


    return render_template(
        "shows.html",
        shows=shows_paginated,  # Pass the pagination object
        sort_by=sort_by,
        filter_genre=filter_genre,  # Pass current genre filter
        filter_year=filter_year,   # Pass current year filter
        filter_rating=filter_rating,  # Pass current rating filter
        now = now, # Pass now to template,
        all_genres = all_genres
    )
   @app.route("/update", methods=["POST"])
@login_required
@admin_required
def update():
    """Triggers the Celery task to update TV shows."""
    update_tv_shows.delay()
    flash('TV shows update has been initiated.', 'success')
    return redirect(url_for('admin'))

@app.route("/test_celery")
@login_required
@admin_required
def test_celery():
    """Triggers a test Celery task (for debugging)."""
    result = test_task.delay()
    return f"Celery test task initiated. Check logs/flower. Task ID: {result.id}", 200

@app.route("/delete_all", methods=["POST"])
@login_required
@admin_required
def delete_all_shows():
    """Deletes ALL TV shows from the database (use with caution!)."""
    try:
        num_rows_deleted = db.session.query(TVShow).delete()
        db.session.commit()
        return jsonify({"message": f"All {num_rows_deleted} shows deleted."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Error deleting shows: {str(e)}"}), 500

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    """Admin dashboard: allows adding, editing, and deleting shows and episodes."""
    add_show_form = AddShowForm()
    add_episode_form = AddEpisodeForm()
    add_episode_form.show_id.choices = [(show.id, show.title) for show in Show.query.all()]

    if add_show_form.validate_on_submit() and add_show_form.submit.data:
        new_show = Show(
            title=add_show_form.title.data,
            overview=add_show_form.overview.data,
            release_year=add_show_form.release_year.data,
            genre=add_show_form.genre.data,
            image_url=add_show_form.image_url.data,
            trailer_url=add_show_form.trailer_url.data,
            imdb_id=add_show_form.imdb_id.data,
            download_link=add_show_form.download_link.data,
            available_seasons=add_show_form.available_seasons.data,
            is_new=add_show_form.is_new.data,
            on_slider=add_show_form.on_slider.data,
        )
        db.session.add(new_show)
        db.session.commit()
        flash('New show added successfully!', 'success')
        return redirect(url_for('admin'))

    if add_episode_form.validate_on_submit() and add_episode_form.submit.data:
        new_episode = Episodes(
            title = add_episode_form.title.data,
            episode_number = add_episode_form.episode_number.data,
            season_number = add_episode_form.season_number.data,
            show_id = add_episode_form.show_id.data,
            download_link = add_episode_form.download_link.data,
            overview = add_episode_form.overview.data,
        )
        db.session.add(new_episode)
        db.session.commit()
        flash('New episode added successfully!', 'success')
        return redirect(url_for('admin'))

    shows = Show.query.all()
    episodes = Episodes.query.all()
    return render_template('admin.html', add_show_form=add_show_form, add_episode_form=add_episode_form, shows=shows, episodes=episodes)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    """Admin login route."""
    if current_user.is_authenticated:
        return redirect(url_for('admin'))  # Already logged in
    form = AdminLoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                return abort(400)  # Prevent open redirects
            flash('Login successful!', 'success')
            return redirect(next_page or url_for('admin'))
        else:
            flash('Invalid username or password.', 'danger')
    return render_template('admin_login.html', form=form)

@app.route('/admin/logout')
@login_required
def admin_logout():
    """Admin logout route."""
    logout_user()
    flash('You have been logged out.', 'success')
    return redirect(url_for('index'))

@app.route('/admin/delete-show/<int:show_id>', methods=['POST'])
@login_required
@admin_required
def delete_show(show_id):
    """Deletes a show and its associated episodes."""
    show = Show.query.get_or_404(show_id)
    # Delete associated episodes first (foreign key constraint)
    for episode in show.episodes:
        db.session.delete(episode)
    db.session.delete(show)
    db.session.commit()
    flash(f'Show "{show.title}" and all its episodes deleted successfully!', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/delete-episode/<int:episode_id>', methods=['POST'])
@login_required
@admin_required
def delete_episode(episode_id):
    """Deletes an episode."""
    episode = Episodes.query.get_or_404(episode_id)
    db.session.delete(episode)
    db.session.commit()
    flash('Episode deleted successfully!', 'success')
    return redirect(url_for('admin'))

@app.route('/admin/edit-show/<int:show_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_show(show_id):
    """Edits an existing show."""
    show = Show.query.get_or_404(show_id)
    form = AddShowForm(obj=show)  # Pre-populate the form

    if form.validate_on_submit():
        show.title = form.title.data
        show.overview = form.overview.data
        show.release_year = form.release_year.data
        show.genre = form.genre.data
        show.image_url = form.image_url.data
        show.trailer_url = form.trailer_url.data
        show.imdb_id = form.imdb_id.data
        show.download_link = form.download_link.data
        show.available_seasons = form.available_seasons.data
        show.is_new = form.is_new.data
        show.on_slider = form.on_slider.data

        db.session.commit()
        flash(f'Show "{show.title}" updated successfully!', 'success')
        return redirect(url_for('admin'))

    return render_template('edit_show.html', form=form, show=show)

@app.route('/admin/edit-episode/<int:episode_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_episode(episode_id):
    """Edits an existing episode."""
    episode = Episodes.query.get_or_404(episode_id)
    form = AddEpisodeForm(obj=episode)  # Pre-populate the form
    form.show_id.choices = [(show.id, show.title) for show in Show.query.all()]  # Show choices

    if form.validate_on_submit():
        episode.title = form.title.data
        episode.episode_number = form.episode_number.data
        episode.season_number = form.season_number.data
        episode.show_id = form.show_id.data
        episode.download_link = form.download_link.data
        episode.overview = form.overview.data
        db.session.commit()
        flash('Episode updated successfully!', 'success')
        return redirect(url_for('admin'))

    return render_template('edit_episode.html', form=form, episode=episode)

# --- Error Handlers ---

@app.errorhandler(404)
def page_not_found(e):
    """Handles 404 errors (page not found)."""
    return render_template("404.html"), 404

@app.errorhandler(500)
def internal_server_error(e):
    """Handles 500 errors (internal server error)."""
    return render_template("500.html"), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
