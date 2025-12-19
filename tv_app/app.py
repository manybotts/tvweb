# =================================================================
# tv_app/app.py - PART 1: IMPORTS, HELPERS & PUBLIC INTERFACE
# =================================================================
import os
import logging
import hashlib
import json
from datetime import datetime
from urllib.parse import urlencode, urlparse

from flask import (
    Flask, render_template, redirect, url_for, request,
    jsonify, send_from_directory, Response, make_response, session, abort
)
from sqlalchemy import func
from dotenv import load_dotenv
from redis import Redis
from werkzeug.exceptions import NotFound

# --- UPDATED IMPORTS (Harmonized with Movie Features) ---
from .models import db, TVShow, Genre, SkippedFile
from .tasks import celery, update_tv_shows, test_task

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
    """Determines if we are on 'tv', 'anime', or 'movie' based on subdomain/host."""
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

@app.template_filter('format_number')
def format_number(value):
    """Format large numbers for view counts (e.g., 1.5K, 2M)."""
    try:
        value = float(value)
        if value >= 1_000_000: return f"{value / 1_000_000:.1f}M"
        elif value >= 1_000: return f"{value / 1_000:.1f}K"
        else: return str(int(value))
    except (ValueError, TypeError): return value

# ----------------------------- Public pages -----------------------------

@app.route('/')
def index():
    mode = get_site_mode()
    other_mode = 'anime' if mode == 'tv' else 'tv'
    search_query = (request.args.get('search') or '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    base_query = TVShow.query.filter(TVShow.category == mode)
    trending_shows = get_trending_shows(limit=6, category=mode)
    message, other_count = None, 0

    if search_query:
        try:
            # Postgres fuzzy search
            shows = base_query.filter(func.similarity(TVShow.show_name, search_query) > 0.1)\
                              .order_by(func.similarity(TVShow.show_name, search_query).desc())\
                              .paginate(page=page, per_page=per_page, error_out=False)
            
            if not shows.items:
                # Fallback to ILIKE
                shows = base_query.filter(TVShow.show_name.ilike(f'%{search_query}%'))\
                                  .order_by(TVShow.created_at.desc())\
                                  .paginate(page=page, per_page=per_page, error_out=False)
                if not shows.items:
                    shows = base_query.order_by(TVShow.created_at.desc())\
                                      .paginate(page=page, per_page=per_page, error_out=False)
                    message = f"No matches found in {mode.upper()}. Showing recent additions."
            
            other_count = TVShow.query.filter(TVShow.category == other_mode, 
                                              TVShow.show_name.ilike(f'%{search_query}%')).count()
        except Exception as e:
            logger.error(f"Search error: {e}")
            shows = base_query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    else:
        shows = base_query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    page_title = f"Search Results: {search_query}" if search_query else ("Anime Library" if mode == 'anime' else "TV Library")
    canonical_url, prev_url, next_url, meta_robots = _page_urls('index', shows, extra_params={'search': search_query})
    
    return render_template('index.html', shows=shows, search_query=search_query, trending_shows=trending_shows, 
                           message=message, title=page_title, site_mode=mode, other_mode=other_mode, 
                           other_count=other_count, canonical_url=canonical_url, prev_url=prev_url, 
                           next_url=next_url, meta_robots=meta_robots)

@app.route('/shows')
def list_shows():
    """Stable Route for TV/Anime filtering."""
    try:
        mode = get_site_mode()
        page, per_page = request.args.get('page', 1, type=int), 30
        genre_filter = request.args.get('genre')
        rating_filter = request.args.get('rating', type=int)
        year_filter = request.args.get('year', type=int)
        sort_by = request.args.get('sort_by', 'name_asc')

        query = TVShow.query.filter(TVShow.category == mode)
        
        # M2M Genre Filter Fix
        if genre_filter:
            query = query.filter(TVShow.genres.any(Genre.name == genre_filter))
        if year_filter:
            query = query.filter(TVShow.year == year_filter)
        if rating_filter is not None:
            query = query.filter(TVShow.rating >= float(rating_filter))

        if sort_by == 'name_asc': query = query.order_by(TVShow.show_name.asc())
        elif sort_by == 'name_desc': query = query.order_by(TVShow.show_name.desc())
        elif sort_by == 'date_desc': query = query.order_by(TVShow.created_at.desc())
        elif sort_by == 'rating_desc': query = query.order_by(TVShow.rating.desc().nullslast())

        shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        all_genres = Genre.query.order_by(Genre.name).all()
        current_year = datetime.utcnow().year
        min_year_result = db.session.query(func.min(TVShow.year)).filter(TVShow.year.isnot(None)).scalar()
        min_year = min_year_result if min_year_result is not None else current_year - 20
        years_list = list(range(current_year, min_year - 1, -1))
        ratings_list = list(range(10, -1, -1))

        canonical_url, prev_url, next_url, meta_robots = _page_urls('list_shows', shows_paginated, 
            extra_params={'genre': genre_filter, 'rating': rating_filter, 'year': year_filter, 'sort_by': sort_by})
        
        return render_template('shows.html', shows=shows_paginated, genres=all_genres, ratings=ratings_list, 
                               years=years_list, selected_genre=genre_filter, selected_rating=rating_filter, 
                               selected_year=year_filter, current_sort_by=sort_by, title="Library", 
                               canonical_url=canonical_url, prev_url=prev_url, next_url=next_url, meta_robots=meta_robots)
    except Exception as e:
        logger.error(f"Error in list_shows: {e}")
        db.session.rollback()
        return render_template('500.html'), 500

@app.route('/movies')
def list_movies():
    """NEW: Movie Library Interface."""
    try:
        page, per_page = request.args.get('page', 1, type=int), 30
        genre_filter = request.args.get('genre')
        rating_filter = request.args.get('rating', type=int)
        year_filter = request.args.get('year', type=int)
        sort_by = request.args.get('sort_by', 'date_desc')

        query = TVShow.query.filter(TVShow.category == 'movie')
        
        if genre_filter:
            query = query.filter(TVShow.genres.any(Genre.name == genre_filter))
        if year_filter:
            query = query.filter(TVShow.year == year_filter)
        if rating_filter is not None:
            query = query.filter(TVShow.rating >= float(rating_filter))

        if sort_by == 'name_asc': query = query.order_by(TVShow.show_name.asc())
        elif sort_by == 'date_desc': query = query.order_by(TVShow.created_at.desc())
        elif sort_by == 'rating_desc': query = query.order_by(TVShow.rating.desc().nullslast())

        shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        all_genres = Genre.query.order_by(Genre.name).all()
        years_list = list(range(datetime.utcnow().year, 1970, -1))
        ratings_list = list(range(10, -1, -1))

        canonical_url, prev_url, next_url, meta_robots = _page_urls('list_movies', shows_paginated, 
            extra_params={'genre': genre_filter, 'year': year_filter, 'rating': rating_filter, 'sort_by': sort_by})
            
        return render_template('movies.html', shows=shows_paginated, genres=all_genres, years=years_list, 
                               ratings=ratings_list, selected_genre=genre_filter, selected_year=year_filter, 
                               selected_rating=rating_filter, current_sort_by=sort_by, title="Movies Library", 
                               canonical_url=canonical_url, prev_url=prev_url, next_url=next_url, meta_robots=meta_robots)
    except Exception as e:
        logger.error(f"Movies list error: {e}")
        return render_template('500.html'), 500

# =================================================================
# END OF PART 1 - PUBLIC INTERFACE
# =================================================================
# =================================================================
# tv_app/app.py - PART 2: DETAILS, REDIRECTS, SEO & NUKE PANEL
# =================================================================

@app.route('/show/<slug>')
def show_details(slug):
    """Detailed view for TV, Anime, or Movies."""
    try:
        show = TVShow.query.filter_by(slug=slug).first_or_404()
        # Increment clicks for trending logic
        show.clicks = (show.clicks or 0) + 1
        db.session.commit()

        title_parts = [show.show_name]
        if show.episode_title:
            title_parts.append(show.episode_title)
        title_parts.append("Details & Download")
        page_title = " - ".join(title_parts)

        # Meta description logic
        if show.overview:
            meta_desc = show.overview[:155] + "..." if len(show.overview) > 155 else show.overview
        else:
            meta_desc = f"View details and download {show.show_name} on iBOX TV."

        # Fetch related content (random from same genres/category)
        related = []
        if show.genres:
            genre_ids = [g.id for g in show.genres]
            related = TVShow.query.filter(
                TVShow.genres.any(Genre.id.in_(genre_ids)), 
                TVShow.id != show.id,
                TVShow.category == show.category
            ).order_by(func.random()).limit(6).all()

        return render_template('show_details.html',
            show=show, related=related, title=page_title, 
            meta_description=meta_desc, canonical_url=request.url, meta_robots="index,follow"
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in show_details slug={slug}: {e}")
        return render_template('500.html'), 500

@app.route('/show/<int:show_id>')
def show_legacy_id(show_id):
    """Redirects old ID-based URLs to new Slug-based URLs for SEO."""
    show = TVShow.query.get_or_404(show_id)
    if getattr(show, 'slug', None):
        return redirect(url_for('show_details', slug=show.slug), code=301)
    return render_template('show_details.html', show=show)

@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    """Controlled redirect handler for Telegram Bot Handshake."""
    try:
        show = TVShow.query.get_or_404(show_id)
        if show.download_link:
            # If it's a direct link or a bot search link, we send them there
            return redirect(show.download_link)
        return "Download link not found", 404
    except NotFound:
        return "Not found", 404

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html', title="Privacy Policy")

@app.route('/update', methods=['POST'])
def update():
    """Trigger manual Telegram update via Celery."""
    try:
        update_tv_shows.delay()
        return jsonify({'message': 'Update initiated'}), 202
    except Exception as e:
        logger.error(f"Failed to initiate update: {e}")
        return jsonify({'message': 'Error'}), 500

# ----------------------------- Nuke panel (auth + backfill) -----------------------------

def _redis():
    return Redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379/0'), decode_responses=True)

def _admin_token():
    return os.environ.get('ADMIN_TOKEN', '')

def _nuke_cookie_ttl_days():
    try:
        return int(os.environ.get('NUKE_COOKIE_TTL_DAYS', '30'))
    except Exception:
        return 30

def _nuke_enabled():
    r = _redis()
    val = r.get('nuke:enabled')
    if val is None:
        r.set('nuke:enabled', '1')
        return True
    return val == '1'

def _nuke_disable(): _redis().set('nuke:enabled', '0')
def _nuke_enable(): _redis().set('nuke:enabled', '1')
def _fail_key(ip): return f"nuke:fail:{ip}"

def _cookie_value():
    secret = app.config['SECRET_KEY']
    token = _admin_token()
    return hashlib.sha256(f"{token}:{secret}".encode()).hexdigest()

def _is_authed(req):
    return req.cookies.get('nuke_auth') == _cookie_value()

@app.route('/nuke', methods=['GET'])
def nuke_home():
    if not _nuke_enabled():
        return render_template('maintenance.html', title="Maintenance"), 503

    if not _is_authed(request):
        msg = request.args.get('msg', '')
        return render_template('nuke_login.html', title="Access Nuke", message=msg)

    # Fetch Skipped Files for Dashboard
    skipped_files = []
    try:
        skipped_files = SkippedFile.query.order_by(SkippedFile.created_at.desc()).limit(100).all()
    except Exception: pass

    q = (request.args.get('q') or '').strip()
    view_dupes = request.args.get('dupes')
    if not q and view_dupes is None: view_dupes = '1'

    if view_dupes:
        rows = db.session.query(
            TVShow.download_link, func.count(TVShow.id).label('cnt')
        ).filter(TVShow.download_link.isnot(None)).group_by(TVShow.download_link)\
         .having(func.count(TVShow.id) > 1).order_by(func.count(TVShow.id).desc()).all()

        dupe_groups = []
        for link, _cnt in rows:
            shows = TVShow.query.filter_by(download_link=link).order_by(TVShow.created_at.desc()).all()
            dupe_groups.append({
                'link': link,
                'domain': urlparse(link).netloc if link else '',
                'shows': shows
            })
        return render_template('nuke.html', title="Nuke", view_dupes=True, dupe_groups=dupe_groups, q=q, skipped_files=skipped_files)

    page = request.args.get('page', 1, type=int)
    query = TVShow.query
    if q:
        try:
            query = query.filter(func.similarity(TVShow.show_name, q) > 0.1).order_by(func.similarity(TVShow.show_name, q).desc())
        except Exception:
            query = query.filter(TVShow.show_name.ilike(f"%{q}%")).order_by(TVShow.created_at.desc())
    else:
        query = query.order_by(TVShow.created_at.desc())

    shows = query.paginate(page=page, per_page=30, error_out=False)
    return render_template('nuke.html', title="Nuke", shows=shows, q=q, view_dupes=False, skipped_files=skipped_files)

@app.route('/nuke/login', methods=['POST'])
def nuke_login():
    if not _nuke_enabled(): return render_template('maintenance.html'), 503
    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '0.0.0.0').split(',')[0].strip()
    token = (request.form.get('token') or '').strip()
    if token != _admin_token():
        r = _redis(); fk = _fail_key(ip); fails = int(r.incr(fk)); r.expire(fk, 3600)
        if fails >= 2: _nuke_disable() # Strict lockout from Base version
        return redirect(url_for('nuke_home', msg=f"Invalid token. Attempt {fails}/2"))
    resp = make_response(redirect(url_for('nuke_home')))
    resp.set_cookie('nuke_auth', _cookie_value(), max_age=_nuke_cookie_ttl_days()*86400, httponly=True, samesite='Lax', secure=True)
    _redis().delete(_fail_key(ip))
    return resp

@app.route('/nuke/delete/<int:show_id>', methods=['POST'])
def nuke_delete(show_id):
    if not _is_authed(request): return abort(403)
    show = TVShow.query.get_or_404(show_id)
    db.session.delete(show); db.session.commit()
    return redirect(url_for('nuke_home', msg=f"Deleted {show.show_name}"))

@app.route('/nuke/clear_skipped', methods=['POST'])
def nuke_clear_skipped():
    """Clear the SkippedFile log."""
    if not _is_authed(request): return abort(403)
    try:
        db.session.query(SkippedFile).delete(); db.session.commit()
    except Exception as e:
        db.session.rollback(); return redirect(url_for('nuke_home', msg=f"Error: {e}"))
    return redirect(url_for('nuke_home', msg="Skipped log cleared"))

# --- MOVIE BACKFILL API ---
@app.route('/nuke/backfill/<action>', methods=['POST'])
def control_backfill(action):
    if not _is_authed(request): return jsonify({'error': 'Unauthorized'}), 401
    r = _redis()
    if action == 'start':
        r.hset("backfill:status", "state", "running")
        celery.send_task('tv_app.tasks.backfill_movies_task')
    elif action == 'pause': r.hset("backfill:status", "state", "paused")
    elif action == 'stop': r.hset("backfill:status", "state", "stopped")
    elif action == 'reset':
        for k in ["backfill:checkpoint_id", "backfill:added", "backfill:skipped"]: r.delete(k)
        r.hmset("backfill:status", {"state": "reset", "progress": 0, "total": 0, "current": "Ready"})
    return jsonify({'status': 'ok', 'action': action})

@app.route('/nuke/backfill/status')
def backfill_status():
    if not _is_authed(request): return jsonify({'error': 'Unauthorized'}), 401
    r = _redis(); st = r.hgetall("backfill:status")
    return jsonify({
        'state': st.get('state', 'idle'),
        'current': st.get('current', '...'),
        'progress': st.get('progress', 0),
        'total': st.get('total', 1),
        'added': r.get("backfill:added") or 0,
        'skipped': r.get("backfill:skipped") or 0
    })

# ----------------------------- SEO Assets -----------------------------
@app.route('/ads.txt')
def ads_txt_redirect():
    """Working Redirect from Base version."""
    return redirect("https://srv.adstxtmanager.com/75094/ibox-tv.com", code=301)

@app.route('/robots.txt')
def robots_txt(): return send_from_directory(app.static_folder, 'robots.txt', mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    try:
        items = TVShow.query.order_by(TVShow.created_at.desc()).limit(50000).all()
        urlset = [f"<url><loc>{url_for('index', _external=True)}</loc><changefreq>hourly</changefreq></url>"]
        for s in items:
            loc = url_for('show_details', slug=s.slug, _external=True)
            urlset.append(f"<url><loc>{loc}</loc><lastmod>{s.created_at.date().isoformat()}</lastmod><changefreq>weekly</changefreq></url>")
        xml = "<?xml version='1.0' encoding='UTF-8'?>\n<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>\n" + "\n".join(urlset) + "\n</urlset>"
        return Response(xml, mimetype="application/xml")
    except Exception as e:
        logger.error(f"Sitemap error: {e}"); return Response("", mimetype="application/xml")

@app.errorhandler(404)
def p404(e): return render_template('404.html', title="404 Not Found"), 404

@app.errorhandler(500)
def p500(e):
    db.session.rollback(); return render_template('500.html', title="500 Server Error"), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)

# =================================================================
# END OF PART 2 - FULLY HARMONIZED
# =================================================================
