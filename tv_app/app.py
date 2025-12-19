# --- tv_app/app.py (PART 1: Config & Public Routes) ---
import os
import logging
import hashlib
import json
from datetime import datetime
from urllib.parse import urlencode, urlparse

from flask import (
    Flask, render_template, redirect, url_for, request,
    jsonify, send_from_directory, Response, make_response
)
from sqlalchemy import func
from dotenv import load_dotenv
from redis import Redis
from werkzeug.exceptions import NotFound

# Added SkippedFile to imports
from .models import db, TVShow, Genre, SkippedFile

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///tv_shows.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- HELPERS ---

def get_site_mode():
    """Determines if we are on 'tv' or 'anime' based on subdomain."""
    host = request.host.lower()
    if 'anime' in host:
        return 'anime'
    return 'tv'

@app.context_processor
def inject_globals():
    """Injects 'now' and 'site_mode' into every template."""
    return {
        'now': datetime.utcnow,
        'site_mode': get_site_mode()
    }

def get_trending_shows(limit: int = 6, category: str = 'tv'):
    """Fetches top clicked shows FOR THE CURRENT CATEGORY only."""
    with app.app_context():
        return TVShow.query.filter_by(category=category)\
                     .order_by(TVShow.clicks.desc())\
                     .limit(limit).all()

def _page_urls(base_endpoint: str, page_obj, extra_params=None):
    extra_params = extra_params or {}
    def _u(p):
        params = {**extra_params, 'page': p}
        return url_for(base_endpoint, _external=True, **params)
    prev_url = _u(page_obj.prev_num) if page_obj.has_prev else None
    next_url = _u(page_obj.next_num) if page_obj.has_next else None
    canonical_url = _u(page_obj.page)
    meta_robots = "index,follow" if page_obj.page == 1 else "noindex,follow"
    return canonical_url, prev_url, next_url, meta_robots

@app.template_filter('hostonly')
def hostonly(url):
    try:
        return urlparse(url).netloc or '—'
    except Exception:
        return '—'

# ----------------------------- Public pages -----------------------------

@app.route('/')
def index():
    mode = get_site_mode() # 'tv' or 'anime'
    other_mode = 'anime' if mode == 'tv' else 'tv'
    
    search_query = (request.args.get('search') or '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    # Base query filters by the current site mode
    # IMPORTANT: This naturally excludes movies (category='movie')
    base_query = TVShow.query.filter(TVShow.category == mode)

    # Trending logic (scoped to current category)
    trending_shows = get_trending_shows(limit=6, category=mode)
    
    message = None
    shows = None
    other_count = 0

    if search_query:
        # 1. SEARCH CURRENT CATEGORY
        try:
            # Try Postgres fuzzy search first
            shows = base_query.filter(
                func.similarity(TVShow.show_name, search_query) > 0.1
            ).order_by(
                func.similarity(TVShow.show_name, search_query).desc()
            ).paginate(page=page, per_page=per_page, error_out=False)

            if not shows.items:
                # Fallback to ILIKE
                shows = base_query.filter(
                    TVShow.show_name.ilike(f'%{search_query}%')
                ).order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

                if not shows.items:
                    # If still nothing, show latest but warn user
                    shows = base_query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
                    message = f"No matches found in {mode.upper()}. Showing recent additions."
                    page_title = f"No Results for '{search_query}'"
        except Exception as e:
            logger.error(f"Database error during search: {e}")
            shows = base_query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            message = "An error occurred. Showing recent additions."
            page_title = "Search Error"

        if not message:
            page_title = f"Search Results: {search_query}"

        # 2. PEEK AT OTHER CATEGORY (Lightweight Count)
        try:
            other_count = TVShow.query.filter(
                TVShow.category == other_mode,
                TVShow.show_name.ilike(f'%{search_query}%')
            ).count()
        except Exception:
            other_count = 0

    else:
        # Default Homepage View (No Search)
        shows = base_query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
        page_title = "Search & Download Latest Anime" if mode == 'anime' else "Search & Download Latest TV Shows"

    canonical_url, prev_url, next_url, meta_robots = _page_urls('index', shows, extra_params={'search': search_query})
    
    return render_template('index.html',
        shows=shows, search_query=search_query, trending_shows=trending_shows,
        message=message, title=page_title, site_mode=mode,
        other_mode=other_mode, other_count=other_count,
        canonical_url=canonical_url, prev_url=prev_url, next_url=next_url, meta_robots=meta_robots
    )

@app.route('/shows')
def list_shows():
    """Legacy browse route for TV/Anime only."""
    try:
        mode = get_site_mode() # 'tv' or 'anime'
        
        page = request.args.get('page', 1, type=int)
        per_page = 30
        genre_filter = request.args.get('genre')
        rating_filter = request.args.get('rating', type=int)
        year_filter = request.args.get('year', type=int)
        sort_by = request.args.get('sort_by', 'name_asc')

        # Strictly filter by current site mode (excludes movies)
        query = TVShow.query.filter(TVShow.category == mode)
        
        if genre_filter:
            query = query.join(TVShow.genres).filter(Genre.name == genre_filter)
        if year_filter:
            query = query.filter(TVShow.year == year_filter)
        if rating_filter is not None:
            lower = float(rating_filter)
            if rating_filter == 10:
                query = query.filter(TVShow.rating >= lower)
            else:
                query = query.filter(TVShow.rating >= lower, TVShow.rating < lower + 1.0)

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

        # Get genres only for this category if possible, but global is fine for now
        all_genres = Genre.query.order_by(Genre.name).all()
        current_year = datetime.utcnow().year
        min_year_result = db.session.query(func.min(TVShow.year)).filter(TVShow.year.isnot(None)).scalar()
        min_year = min_year_result if min_year_result is not None else current_year - 20
        years = list(range(current_year, min_year - 1, -1))
        possible_ratings = list(range(10, -1, -1))
        
        page_title = "Available Anime" if mode == 'anime' else "Available TV Shows"

        canonical_url, prev_url, next_url, meta_robots = _page_urls('list_shows', shows_paginated, extra_params={
            'genre': genre_filter or '',
            'rating': rating_filter if rating_filter is not None else '',
            'year': year_filter if year_filter is not None else '',
            'sort_by': sort_by
        })
        return render_template('shows.html',
            shows=shows_paginated, genres=all_genres, ratings=possible_ratings, years=years,
            selected_genre=genre_filter, selected_rating=rating_filter, selected_year=year_filter,
            current_sort_by=sort_by, title=page_title,
            canonical_url=canonical_url, prev_url=prev_url, next_url=next_url, meta_robots=meta_robots
        )
    except Exception as e:
        logger.error(f"Error in list_shows route: {e}")
        db.session.rollback()
        return render_template('500.html', title="Server Error",
                               meta_description="An error occurred viewing shows list."), 500

# --- NEW: Movies Route ---
@app.route('/movies')
def list_movies():
    try:
        page = request.args.get('page', 1, type=int)
        per_page = 24  # Grid layout needs multiples of 3/4
        search_q = (request.args.get('q') or '').strip()
        sort_by = request.args.get('sort_by', 'date_desc')
        year_filter = request.args.get('year', type=int)
        rating_filter = request.args.get('rating', type=int)

        # 1. Base Query: Only Movies
        query = TVShow.query.filter(TVShow.category == 'movie')

        # 2. Search Logic
        if search_q:
            try:
                query = query.filter(func.similarity(TVShow.show_name, search_q) > 0.1)
                # Sort by similarity if searching
                query = query.order_by(func.similarity(TVShow.show_name, search_q).desc())
            except Exception:
                query = query.filter(TVShow.show_name.ilike(f'%{search_q}%'))

        # 3. Filters
        if year_filter:
            query = query.filter(TVShow.year == year_filter)
        if rating_filter is not None:
             query = query.filter(TVShow.rating >= float(rating_filter))

        # 4. Sorting (Only apply if NOT searching by relevance)
        if not search_q:
            if sort_by == 'name_asc':
                query = query.order_by(TVShow.show_name.asc())
            elif sort_by == 'rating_desc':
                query = query.order_by(TVShow.rating.desc().nullslast())
            else: # date_desc default
                query = query.order_by(TVShow.created_at.desc())

        movies = query.paginate(page=page, per_page=per_page, error_out=False)

        # Metadata for filters
        current_year = datetime.utcnow().year
        years = list(range(current_year, 1970, -1))
        
        canonical_url, prev_url, next_url, meta_robots = _page_urls('list_movies', movies, extra_params={
            'q': search_q, 'sort_by': sort_by, 'year': year_filter, 'rating': rating_filter
        })

        return render_template('movies.html',
            movies=movies, years=years,
            search_q=search_q, current_sort=sort_by, selected_year=year_filter, selected_rating=rating_filter,
            title="Browse Movies - Download & Stream",
            canonical_url=canonical_url, prev_url=prev_url, next_url=next_url, meta_robots=meta_robots
        )
    except Exception as e:
        logger.error(f"Error in list_movies: {e}")
        return render_template('500.html'), 500

@app.route('/show/<slug>')
def show_details(slug):
    try:
        show = TVShow.query.filter_by(slug=slug).first_or_404()
        show.clicks = (show.clicks or 0) + 1
        db.session.commit()

        # Handle different title formats based on category
        if show.category == 'movie':
             title_parts = [show.show_name, f"({show.year})" if show.year else "", "Movie Download"]
        else:
             title_parts = [show.show_name]
             if show.episode_title: title_parts.append(show.episode_title)
             title_parts.append("Details & Download")
        
        page_title = " ".join([p for p in title_parts if p])

        if show.overview:
            meta_desc = (show.overview[:155] + "...") if len(show.overview) > 155 else show.overview
        else:
            meta_desc = f"View details and download {show.show_name} on iBOX TV."
        
        return render_template('show_details.html',
            show=show, title=page_title, meta_description=meta_desc,
            canonical_url=request.url, meta_robots="index,follow"
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in show_details slug={slug}: {e}")
        return render_template('500.html'), 500

@app.route('/show/<int:show_id>')
def show_legacy_id(show_id):
    show = TVShow.query.get_or_404(show_id)
    if getattr(show, 'slug', None):
        return redirect(url_for('show_details', slug=show.slug), code=301)
    return render_template('show_details.html', show=show, title=show.show_name, meta_robots="noindex,follow")

@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    try:
        show = TVShow.query.get_or_404(show_id)
        if show.download_link:
            return redirect(show.download_link)
    except NotFound:
        pass
    return "Download link not found for this show.", 404

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html', title="Privacy Policy")
    # --- tv_app/app.py (PART 2: SEO, Nuke Panel & Backfill) ---

# ----------------------------- SEO & Sitemaps -----------------------------

@app.route('/ads.txt')
def ads_txt_redirect():
    """Redirects to the main ads.txt (if you have one managed elsewhere)"""
    return redirect("https://srv.adstxtmanager.com/34887/ibox-tv.com", code=301)

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt')

@app.route('/sitemap.xml')
def sitemap_xml():
    """Generates a dynamic sitemap including standard pages and all show slugs."""
    host = request.host_url.rstrip('/')
    # Static pages
    pages = [
        {'loc': f"{host}/", 'lastmod': datetime.utcnow().strftime('%Y-%m-%d'), 'changefreq': 'daily', 'priority': '1.0'},
        {'loc': f"{host}/shows", 'lastmod': datetime.utcnow().strftime('%Y-%m-%d'), 'changefreq': 'daily', 'priority': '0.9'},
        {'loc': f"{host}/movies", 'lastmod': datetime.utcnow().strftime('%Y-%m-%d'), 'changefreq': 'daily', 'priority': '0.9'},
    ]

    # Dynamic Pages (Recent 5000 to keep it light)
    shows = TVShow.query.order_by(TVShow.updated_at.desc()).limit(5000).all()
    for show in shows:
        pages.append({
            'loc': f"{host}/show/{show.slug}",
            'lastmod': show.updated_at.strftime('%Y-%m-%d'),
            'changefreq': 'weekly',
            'priority': '0.8'
        })

    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for p in pages:
        xml.append(f"  <url><loc>{p['loc']}</loc><lastmod>{p['lastmod']}</lastmod><changefreq>{p['changefreq']}</changefreq><priority>{p['priority']}</priority></url>")
    xml.append('</urlset>')
    
    return Response("\n".join(xml), mimetype='application/xml')

# ----------------------------- Nuke / Admin Panel -----------------------------

NUKE_PASS_HASH = hashlib.sha256(os.environ.get('NUKE_PASS', 'admin').encode()).hexdigest()

def check_auth():
    """Verifies the nuke_token cookie."""
    token = request.cookies.get('nuke_token')
    if not token:
        return False
    # Simple hash check: cookie should match the password hash
    return token == NUKE_PASS_HASH

@app.route('/nuke/login', methods=['GET', 'POST'])
def login_nuke():
    if request.method == 'POST':
        password = request.form.get('password')
        if hashlib.sha256(password.encode()).hexdigest() == NUKE_PASS_HASH:
            resp = make_response(redirect(url_for('nuke_dashboard')))
            # Set cookie for 1 year
            resp.set_cookie('nuke_token', NUKE_PASS_HASH, max_age=60*60*24*365, httponly=True)
            return resp
        else:
            return render_template('nuke_login.html', error="Invalid Password")
    return render_template('nuke_login.html')

@app.route('/nuke')
def nuke_dashboard():
    if not check_auth():
        return redirect(url_for('login_nuke'))
    
    # Dashboard Stats
    total_tv = TVShow.query.filter_by(category='tv').count()
    total_anime = TVShow.query.filter_by(category='anime').count()
    total_movies = TVShow.query.filter_by(category='movie').count()
    
    # Fetch recent skipped files for debugging
    skipped_files = SkippedFile.query.order_by(SkippedFile.created_at.desc()).limit(50).all()

    return render_template('nuke.html', 
                           total_tv=total_tv, 
                           total_anime=total_anime, 
                           total_movies=total_movies,
                           skipped_files=skipped_files)

@app.route('/nuke/api/search')
def api_nuke_search():
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
        
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify([])
    
    # Admin search searches everything
    results = TVShow.query.filter(TVShow.show_name.ilike(f'%{query}%')).limit(20).all()
    
    data = []
    for s in results:
        data.append({
            'id': s.id,
            'title': s.show_name,
            'episode': s.episode_title,
            'category': s.category,
            'slug': s.slug
        })
    return jsonify(data)

@app.route('/nuke/delete/<int:show_id>', methods=['POST'])
def api_delete_show(show_id):
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    
    show = TVShow.query.get_or_404(show_id)
    try:
        db.session.delete(show)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Deleted {show.show_name}'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)})

# --- Update Controls ---

@app.route('/nuke/trigger_update', methods=['POST'])
def trigger_manual_update():
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        # Lazy import to avoid circular dependency
        from .tasks import update_tv_shows
        update_tv_shows.delay()
        return jsonify({'success': True, 'message': 'Update Task Started'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

# --- NEW: Backfill Controls ---

def get_redis():
    """Helper to get a thread-safe Redis connection."""
    return Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

@app.route('/nuke/backfill/start', methods=['POST'])
def trigger_backfill():
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        from .tasks import backfill_movies_task
        # Clear the pause flag if it exists
        r = get_redis()
        r.delete('backfill:pause')
        
        backfill_movies_task.delay()
        return jsonify({'success': True, 'message': 'Backfill Task Started'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/nuke/backfill/pause', methods=['POST'])
def pause_backfill():
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        r = get_redis()
        # Set pause flag
        r.set('backfill:pause', '1')
        return jsonify({'success': True, 'message': 'Pause Signal Sent'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/nuke/backfill/status')
def backfill_status():
    if not check_auth():
        return jsonify({'error': 'Unauthorized'}), 401
    try:
        r = get_redis()
        # Retrieve status hash
        status = r.hgetall('backfill:status')
        # Convert bytes to string
        status = {k.decode('utf-8'): v.decode('utf-8') for k, v in status.items()}
        return jsonify(status)
    except Exception:
        return jsonify({'state': 'Unknown', 'progress': 0})

# ----------------------------- Error Handlers -----------------------------

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
