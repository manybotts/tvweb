# tv_app/app.py
import os
from flask import Flask, render_template, redirect, url_for, request, jsonify
from .tasks import update_tv_shows, test_task  # Relative import
from .models import db, TVShow  # Relative import
from sqlalchemy import desc
from dotenv import load_dotenv
import logging #Import logging

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///tv_shows.db')  # Default to SQLite
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Database Operations (using SQLAlchemy's built-in features) ---

def get_trending_shows(limit=5):
    """Retrieves the top 'limit' trending shows, ordered by clicks."""
    return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

# --- Routes ---

@app.route('/')
def index():
    """Homepage: displays TV shows with pagination and search, plus trending shows."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    if search_query:
        shows = TVShow.query.filter(TVShow.show_name.ilike(f'%{search_query}%')).paginate(page=page, per_page=per_page, error_out=False)
        if not shows.items: #Use pagination object
          shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
          message = f"No show with name '{search_query}', Here are all available shows!"
          return render_template('index.html', shows=shows, search_query=search_query, trending_shows=[], message=message)
        trending_shows = []
    else:
        shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        trending_shows = get_trending_shows()

    return render_template('index.html', shows=shows, search_query=search_query, trending_shows=trending_shows)


@app.route('/show/<int:show_id>')
def show_details(show_id):
    """Displays details for a single TV show and increments its click count."""
    show = TVShow.query.get_or_404(show_id)  # Use get_or_404 with the primary key
    show.clicks += 1
    db.session.commit()
    return render_template('show_details.html', show=show)


@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    """Redirects to the download link for a TV show."""
    show = TVShow.query.get_or_404(show_id)  # Use get_or_404 with the primary key
    if show.download_link:
        return redirect(show.download_link)
    return "Show or link not found", 404

@app.route('/shows')  # Keep this as is - it lists show *names*, not full details
def list_shows():
    """Displays a list of all available TV show names."""
    show_names = [show.show_name for show in TVShow.query.distinct(TVShow.show_name).order_by(TVShow.show_name).all()]
    return render_template('shows.html', show_names=show_names)

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
