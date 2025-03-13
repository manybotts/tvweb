#tv_app/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from .models import db, TVShow
from sqlalchemy import desc

bp = Blueprint('routes', __name__)

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

@bp.route('/')
def index():
    """Homepage: displays TV shows with pagination and search, plus trending shows."""
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10

    if search_query:
        tv_shows, total_pages = get_all_tv_shows(page, per_page, search_query)
        trending_shows = []  # No trending shows when searching
    else:
        tv_shows, total_pages = get_all_tv_shows(page, per_page)
        trending_shows = get_trending_shows()

    return render_template('index.html', tv_shows=tv_shows, page=page, total_pages=total_pages, search_query=search_query, trending_shows=trending_shows)


@bp.route('/show/<int:message_id>')
def show_details(message_id):
    """Displays details for a single TV show and increments its click count."""
    show = get_tv_show_by_message_id(message_id)
    if show:
        with current_app.app_context():  # Use app context for db access!
            show.clicks += 1
            db.session.commit()
        return render_template('show_details.html', show=show)
    return "Show not found", 404

@bp.route('/redirect/<int:message_id>')
def redirect_to_download(message_id):
    """Redirects to the download link for a TV show."""
    show = get_tv_show_by_message_id(message_id)
    if show and show.download_link:
        return redirect(show.download_link)
    return "Show or link not found", 404

@bp.route('/shows')
def list_shows():
    """Displays a list of all available TV show names."""
    show_names = get_all_show_names()
    return render_template('shows.html', show_names=show_names)

@bp.route('/search')
def search():
    query = request.args.get('query', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    tv_shows, total_pages = get_all_tv_shows(page=page, per_page=per_page, search_query=query)
    return render_template('search_results.html', tv_shows=tv_shows, total_pages=total_pages, current_page=page, search_query=query)


@bp.route('/latest')
def latest_shows():
    page = request.args.get('page', 1, type=int)
    per_page = 10 # Shows per page
    tv_shows, total_pages = get_all_tv_shows(page=page, per_page=per_page)
    return render_template('latest_shows.html', tv_shows=tv_shows, total_pages=total_pages, current_page=page)

@bp.route('/delete/<int:message_id>', methods=['POST'])
def delete_show(message_id):
    show = get_tv_show_by_message_id(message_id)
    if show:
        with current_app.app_context():
            db.session.delete(show)
            db.session.commit()
            return redirect(url_for('routes.index'))
    return "Show not found", 404
# --- Example API endpoint (returning JSON) ---

@bp.route('/api/shows')
def api_shows():
    shows = TVShow.query.all()
    show_list = []
    for show in shows:
        show_list.append({
            'id': show.id,
            'message_id': show.message_id,
            'show_name': show.show_name,
            'episode_title': show.episode_title,
            'download_link': show.download_link,
            'overview': show.overview,
            'poster_path': show.poster_path,
            'vote_average': show.vote_average,
            'created_at': show.created_at.isoformat(),  # Format for JSON
            'clicks': show.clicks
        })
    return jsonify(show_list)
