# tv_app/app.py

# ---- BEGINNING OF PART 1 ----

from flask import (render_template, redirect, url_for, request, jsonify,
                   flash, abort)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
from .models import db, User, Show, Episodes  # Import all models
from .tasks import update_tv_shows, normalize_string  # Import tasks
from sqlalchemy import desc, func
from thefuzz import process, fuzz
import datetime
from functools import wraps
from urllib.parse import urlparse, urljoin
from .forms import AdminLoginForm, AddShowForm, AddEpisodeForm  # Import forms
# No create_app() or app instantiation here!
from flask import Flask
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "your_secret_key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///tv_shows.db").replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# --- Flask-Login Setup ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Helper Functions ---

def get_trending_shows(limit=5):
    return Show.query.order_by(Show.clicks.desc()).limit(limit).all()

def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.username != 'admin':
            flash('You do not have permission to access this page.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

@app.route("/")
def index():
    search_query = request.args.get("search", "")
    page = request.args.get("page", 1, type=int)
    per_page = 10
    message = None

    if search_query:
        normalized_query = normalize_string(search_query)
        exact_match = Show.query.filter(func.lower(Show.title) == normalized_query).first()
        partial_matches = Show.query.filter(Show.title.ilike(f"%{normalized_query}%")).all()
        all_show_names = [show.title for show in Show.query.all()]
        fuzzy_matches = process.extract(normalized_query, all_show_names, scorer=fuzz.token_set_ratio)

        results = []
        if exact_match:
            results.append(exact_match)
        for show in partial_matches:
            if show not in results:
                results.append(show)
        for show_name, score in fuzzy_matches:
            if score >= 60:
                show = Show.query.filter(func.lower(Show.title) == normalize_string(show_name)).first()
                if show and show not in results:
                    results.append(show)

        shows = paginate_results(results, page, per_page)

        if not shows.items:
            message = (
                f"No shows found matching '{search_query}'. Here are some similar shows:"
            )
            shows = Show.query.order_by(Show.clicks.desc()).paginate(page=page, per_page=per_page, error_out=False)
            if not shows.items:
                message = f"No shows found matching '{search_query}'. Displaying all shows."
                shows = (
                    Show.query.order_by(Show.id.desc())
                    .paginate(page=page, per_page=per_page, error_out=False)
                )
        trending_shows = [] #No trending shows if search query exists

    else:
        shows = (
            Show.query.order_by(Show.id.desc())  # Use a simple ordering, like ID.
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
    from flask_sqlalchemy import pagination

    start = (page - 1) * per_page
    end = start + per_page
    paginated_items = results[start:end]

    pagination_obj = CustomPagination(page, per_page, len(results), paginated_items)
    return pagination_obj

class CustomPagination:
    def __init__(self, page, per_page, total, items):
        self.page = page
        self.per_page = per_page
        self.total = total
        self.items = items

    @property
    def pages(self):
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
        last = 0
        for num in range(1, self.pages + 1):
            if num <= left_edge or (num > self.page - left_current - 1 and num < self.page + right_current) or num > self.pages - right_edge:
                if last + 1 != num:
                    yield None
                yield num
                last = num

@app.route("/show/<int:show_id>")
def show_details(show_id):
    show = Show.query.get_or_404(show_id)
    show.clicks += 1  # Increment clicks
    db.session.commit()
    episodes = Episodes.query.filter_by(show_id=show.id).order_by(Episodes.season_number, Episodes.episode_number).all()
    return render_template("show_details.html", show=show, episodes=episodes)

@app.route("/episode/<int:episode_id>")
def episode_details(episode_id):
    episode = Episodes.query.get_or_404(episode_id)
    episode.show.clicks += 1  # Increment clicks on the *show*
    db.session.commit()
    return render_template("episode_details.html", episode=episode)


@app.route("/redirect/<int:episode_id>")
def redirect_to_download(episode_id):
    episode = Episodes.query.get_or_404(episode_id)
    if episode.download_link:
        return redirect(episode.download_link)
    return "Episode or link not found", 404


@app.route("/shows")
def list_shows():
    page = request.args.get('page', 1, type=int)
    per_page = 10  # Or whatever number of shows per page you want
    sort_by = request.args.get('sort', 'name', type=str)
    filter_genre = request.args.get('genre', 'all', type=str)
    filter_year = request.args.get('year', 'all', type=str)
    filter_rating = request.args.get('rating', 'all', type=str)  # Get rating filter

    query = Show.query

    # Filtering
    if filter_genre != 'all':
        query = query.filter(Show.genre.ilike(f"%{filter_genre}%"))  # Use ilike for case-insensitive

    if filter_year != 'all':
        try:
            filter_year_int = int(filter_year)
            query = query.filter(Show.release_year == filter_year_int)
        except ValueError:
            # Handle the case where filter_year isn't a valid integer.
            # You could show an error, or default to 'all', or redirect.
            # Here, we're just going to ignore the invalid year.
            pass

    if filter_rating != 'all':
        try:
            filter_rating_int = int(filter_rating)
            # Assuming you have a way to get a show's rating.  This is a placeholder.
            # You *might* need a separate 'ratings' table, or a computed property.
            # Replace this with your actual rating logic.
            query = query.filter(Show.clicks >= filter_rating_int) #Changed this to click

        except ValueError:
            pass  # Ignore invalid rating values

    # Sorting
    if sort_by == 'name':
        query = query.order_by(Show.title.asc())
    elif sort_by == 'popularity':
        query = query.order_by(Show.clicks.desc()) #Sort by popularity
    elif sort_by == 'year':
        query = query.order_by(Show.release_year.desc())
    # Add other sorting options here (e.g., 'rating') if you have a rating field.

    shows = query.paginate(page=page, per_page=per_page, error_out=False)

    # Get all genres for the filter dropdown
    all_genres = [g[0] for g in db.session.query(Show.genre).distinct().all() if g[0]]
    # Remove duplicates and handle None values
    all_genres = list({genre for genres in all_genres for genre in (genres.split(',') if genres else [])})
    all_genres.sort()  # Alphabetical order

    # Get all the years
    all_years = sorted(db.session.query(Show.release_year).distinct().all(), reverse=True)
    all_years = [year[0] for year in all_years if year[0] is not None]

    return render_template('shows.html', shows=shows, sort_by=sort_by,
                           filter_genre=filter_genre, all_genres=all_genres, filter_year=filter_year,filter_rating = filter_rating, all_years = all_years, now=datetime.datetime.now()) # Pass now to template


# ---- END OF PART 1 ----
# tv_app/app.py

# ---- BEGINNING OF PART 2 ----

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
    result = update_tv_shows.delay()  # Use your actual task
    return f"Celery test task initiated. Check logs/flower. Task ID: {result.id}", 200

@app.route("/delete_all", methods=["POST"])
@login_required
@admin_required
def delete_all_shows():
    """Deletes ALL TV shows and episodes from the database (use with extreme caution!)."""
    try:
        # Delete all episodes first (due to foreign key constraints)
        num_episodes_deleted = db.session.query(Episodes).delete()
        # Then delete all shows
        num_shows_deleted = db.session.query(Show).delete()
        db.session.commit()
        flash(f'All shows ({num_shows_deleted}) and episodes ({num_episodes_deleted}) deleted.', 'success')
        return jsonify({"message": f"All shows and episodes deleted."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Error deleting shows/episodes: {str(e)}"}), 500

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    """Admin dashboard: allows adding, editing, and deleting shows and episodes."""
    add_show_form = AddShowForm()
    add_episode_form = AddEpisodeForm()
    add_episode_form.show_id.choices = [(show.id, show.title) for show in Show.query.all()]

    if add_show_form.validate_on_submit() and add_show_form.submit_show.data:
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

    if add_episode_form.validate_on_submit() and add_episode_form.submit_episode.data:
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
        return redirect(url_for('admin'))
    form = AdminLoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            if not is_safe_url(next_page):
                return abort(400)
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
    Episodes.query.filter_by(show_id=show.id).delete()

    db.session.delete(show)
    db.session.commit()
    flash(f'Show "{show.title}" and all its episodes have been deleted.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/delete-episode/<int:episode_id>', methods=['POST'])
@login_required
@admin_required
def delete_episode(episode_id):
    """Deletes a single episode."""
    episode = Episodes.query.get_or_404(episode_id)
    db.session.delete(episode)
    db.session.commit()
    flash(f'Episode "{episode.title}" (S{episode.season_number:02d}E{episode.episode_number:02d}) has been deleted.', 'success')
    return redirect(url_for('admin'))


@app.route('/admin/edit-show/<int:show_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_show(show_id):
    """Edits an existing show."""
    show = Show.query.get_or_404(show_id)
    form = AddShowForm(obj=show)  #Pre-populates

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
        show.is_new = form.is_new.data  # Get is_new from the form
        show.on_slider = form.on_slider.data  # Get on_slider from the form
        db.session.commit()
        flash(f'Show "{show.title}" updated successfully!', 'success')
        return redirect(url_for('admin'))

    return render_template('edit_show.html', form=form, show=show)

@app.route('/admin/edit-episode/<int:episode_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_episode(episode_id):
    """Edits existing episode"""
    episode = Episodes.query.get_or_404(episode_id)
    form = AddEpisodeForm(obj=episode)
    form.show_id.choices = [(show.id, show.title) for show in Show.query.all()] #Populate

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

# ---- END OF PART 2 ----
