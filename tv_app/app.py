# tv_app/app.py
import os
from flask import Flask, render_template, redirect, url_for, request, jsonify
from .tasks import update_tv_shows, test_task, normalize_string  # Import normalize_string
from .models import db, TVShow
from sqlalchemy import desc
from dotenv import load_dotenv
import logging
from thefuzz import process, fuzz  # Import thefuzz


load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "your_secret_key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///tv_shows.db"
)  # Default to SQLite
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Database Operations ---


def get_trending_shows(limit=5):
    """Retrieves the top 'limit' trending shows, ordered by clicks."""
    return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()


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
        fuzzy_matches = process.extract(normalized_query, all_show_names, limit=5)

        # Combine and Prioritize Results
        results = []
        if exact_match:
            results.append(exact_match)
        for show in partial_matches:
            if show not in results:
                results.append(show)
        for show_name, score in fuzzy_matches:
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
    return render_template("show_details.html", show=show)


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

    # Start with a base query that selects distinct show names.
    query = TVShow.query.distinct(TVShow.show_name)

    # Apply filtering
    if filter_genre:
        query = query.filter(TVShow.genre.ilike(f"%{filter_genre}%"))

    if filter_year:
        try:
            filter_year = int(filter_year)
            query = query.filter(TVShow.year == filter_year)
        except ValueError:
            pass

    if filter_rating:
        try:
            filter_rating = float(filter_rating)
            query = query.filter(TVShow.vote_average >= filter_rating)
        except ValueError:
            pass
    # Apply sorting.  Crucially, we sort *after* filtering.
    if sort_by == "name":
        query = query.order_by(TVShow.show_name)  # Now we can order correctly
    elif sort_by == "popularity":
        query = query.order_by(TVShow.clicks.desc())
    elif sort_by == "year":
        query = query.order_by(TVShow.year.desc())
    elif sort_by == "rating":
        query = query.order_by(TVShow.vote_average.desc())

    # Paginate the query *after* filtering and sorting
    shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)

    return render_template(
        "shows.html",
        shows=shows_paginated,  # Pass the pagination object
        sort_by=sort_by,
        filter_genre=filter_genre,
        filter_year=filter_year,
        filter_rating=filter_rating,
    )


@app.route("/update", methods=["POST"])
def update():
    update_tv_shows.delay()  # Run the task asynchronously
    return jsonify({"message": "Update initiated"}), 202


@app.route("/test_celery")
def test_celery():
    result = test_task.delay()
    return f"Celery test task initiated. Check logs/flower. Task ID: {result.id}", 200


@app.route("/delete_all", methods=["POST"])
def delete_all_shows():
    try:
        num_rows_deleted = db.session.query(TVShow).delete()
        db.session.commit()
        return jsonify({"message": f"All {num_rows_deleted} shows deleted."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": f"Error deleting shows: {str(e)}"}), 500


# --- Error Handlers ---


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_server_error(e):
    return render_template("500.html"), 500
