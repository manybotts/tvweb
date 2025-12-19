# --- tv_app/app.py (PART 1) ---
import os
import logging
import hashlib
import json  # Added
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

# UPDATED IMPORTS
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
    host = request.host.lower()
    if 'anime' in host:
        return 'anime'
    return 'tv'

@app.context_processor
def inject_globals():
    return {
        'now': datetime.utcnow,
        'site_mode': get_site_mode()
    }

def get_trending_shows(limit: int = 6, category: str = 'tv'):
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
    try:
        value = float(value)
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        elif value >= 1_000:
            return f"{value / 1_000:.1f}K"
        else:
            return str(int(value))
    except (ValueError, TypeError):
        return value

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
    
    message = None
    shows = None
    other_count = 0

    if search_query:
        try:
            # Fuzzy Search
            shows = base_query.filter(
                func.similarity(TVShow.show_name, search_query) > 0.1
            ).order_by(
                func.similarity(TVShow.show_name, search_query).desc()
            ).paginate(page=page, per_page=per_page, error_out=False)

            if not shows.items:
                # ILIKE Fallback
                shows = base_query.filter(
                    TVShow.show_name.ilike(f'%{search_query}%')
                ).order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

                if not shows.items:
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

        try:
            other_count = TVShow.query.filter(
                TVShow.category == other_mode,
                TVShow.show_name.ilike(f'%{search_query}%')
            ).count()
        except Exception:
            other_count = 0

    else:
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
    try:
        mode = get_site_mode()
        
        page = request.args.get('page', 1, type=int)
        per_page = 30
        genre_filter = request.args.get('genre')
        rating_filter = request.args.get('rating', type=int)
        year_filter = request.args.get('year', type=int)
        sort_by = request.args.get('sort_by', 'name_asc')

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

        if sort_by == 'name_asc': query = query.order_by(TVShow.show_name.asc())
        elif sort_by == 'name_desc': query = query.order_by(TVShow.show_name.desc())
        elif sort_by == 'date_asc': query = query.order_by(TVShow.created_at.asc())
        elif sort_by == 'date_desc': query = query.order_by(TVShow.created_at.desc())
        elif sort_by == 'rating_asc': query = query.order_by(TVShow.rating.asc().nullslast())
        elif sort_by == 'rating_desc': query = query.order_by(TVShow.rating.desc().nullslast())

        shows_paginated = query.paginate(page=page, per_page=per_page, error_out=False)
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

@app.route('/show/<slug>')
def show_details(slug):
    try:
        show = TVShow.query.filter_by(slug=slug).first_or_404()
        show.clicks = (show.clicks or 0) + 1
        db.session.commit()

        title_parts = [show.show_name]
        if show.episode_title:
            title_parts.append(show.episode_title)
        title_parts.append("Details & Download")
        page_title = " - ".join(title_parts)

        if show.overview:
            meta_desc_content = show.overview[:155] + "..." if len(show.overview) > 155 else show.overview
            meta_desc = f"{meta_desc_content} Find details and download link on iBOX TV."
        else:
            meta_desc = f"View details and download {show.show_name}{' - ' + show.episode_title if show.episode_title else ''} on iBOX TV."
        meta_desc = meta_desc[:160]

        return render_template('show_details.html',
            show=show, title=page_title, meta_description=meta_desc,
            canonical_url=request.url, meta_robots="index,follow"
        )
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error in show_details slug={slug}: {e}")
        return render_template('500.html', title="Server Error",
                               meta_description="An error occurred viewing show details."), 500

@app.route('/show/<int:show_id>')
def show_legacy_id(show_id):
    show = TVShow.query.get_or_404(show_id)
    if getattr(show, 'slug', None):
        return redirect(url_for('show_details', slug=show.slug), code=301)
    title = f"{show.show_name} - Details & Download"
    return render_template('show_details.html', show=show, title=title,
                           meta_description=f"View details and download {show.show_name} on iBOX TV.",
                           canonical_url=request.url, meta_robots="noindex,follow")

@app.route('/redirect/<int:show_id>')
def redirect_to_download(show_id):
    try:
        show = TVShow.query.get_or_404(show_id)
    except NotFound:
        return "Download link not found for this show.", 404
    if show.download_link:
        return redirect(show.download_link)
    return "Download link not found for this show.", 404

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html', title="Privacy Policy")

@app.route('/update', methods=['POST'])
def update():
    try:
        from .tasks import update_tv_shows
        update_tv_shows.delay()
        return jsonify({'message': 'Update initiated'}), 202
    except Exception as e:
        logger.error(f"Failed to initiate update task: {e}")
        return jsonify({'message': 'Error initiating update'}), 500

@app.route('/test_celery')
def test_celery():
    try:
        from .tasks import test_task
        result = test_task.delay()
        return f"Celery test task initiated. Task ID: {result.id}", 200
    except Exception as e:
        logger.error(f"Failed to initiate test task: {e}")
        return jsonify({'message': 'Error initiating test task'}), 500

@app.route('/delete_all', methods=['POST'])
def delete_all_shows():
    try:
        num_rows_deleted = db.session.query(TVShow).delete()
        db.session.commit()
        logger.info(f'All {num_rows_deleted} shows deleted.')
        return jsonify({'message': f'All {num_rows_deleted} shows deleted.'}), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f'Error deleting all shows: {e}')
        return jsonify({'message': f'Error deleting shows: {str(e)}'}), 500

# ----------------------------- SEO assets -----------------------------
@app.route('/ads.txt')
def ads_txt_redirect():
    return redirect("https://srv.adstxtmanager.com/75094/ibox-tv.com", code=301)

@app.route('/robots.txt')
def robots_txt():
    return send_from_directory(app.static_folder, 'robots.txt', mimetype='text/plain')

@app.route('/sitemap.xml')
def sitemap_xml():
    try:
        items = TVShow.query.order_by(
            (TVShow.updated_at.desc() if hasattr(TVShow, 'updated_at') else TVShow.created_at.desc())
        ).limit(50000).all()
        urlset = []
        base = url_for('index', _external=True)
        urlset.append(f"<url><loc>{base}</loc><changefreq>hourly</changefreq></url>")
        for s in items:
            loc = url_for('show_details', slug=s.slug, _external=True)
            lm = getattr(s, 'updated_at', None) or s.created_at or datetime.utcnow()
            lastmod = lm.date().isoformat()
            urlset.append(f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod><changefreq>weekly</changefreq></url>")
        xml = "<?xml version='1.0' encoding='UTF-8'?>\n" \
              "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>\n" + \
              "\n".join(urlset) + "\n</urlset>"
        return Response(xml, mimetype="application/xml")
    except Exception as e:
        logger.error(f"sitemap error: {e}")
        return Response("<?xml version='1.0' encoding='UTF-8'?><urlset/>", mimetype="application/xml")

# --- END PART 1 ---
# --- tv_app/app.py (PART 2) ---

# ----------------------------- Nuke panel (auth + dupes + backfill) -----------------------------
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

def _nuke_disable():
    _redis().set('nuke:enabled', '0')

def _nuke_enable():
    _redis().set('nuke:enabled', '1')

def _fail_key(ip):
    return f"nuke:fail:{ip}"

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

    # --- NEW: Fetch Skipped Files for Dashboard ---
    skipped_files = []
    try:
        skipped_files = SkippedFile.query.order_by(SkippedFile.created_at.desc()).limit(100).all()
    except Exception:
        pass

    q = (request.args.get('q') or '').strip()
    view_dupes = request.args.get('dupes')
    if not q and view_dupes is None:
        view_dupes = '1'

    if view_dupes:
        rows = db.session.query(
            TVShow.download_link, func.count(TVShow.id).label('cnt')
        ).filter(
            TVShow.download_link.isnot(None)
        ).group_by(
            TVShow.download_link
        ).having(
            func.count(TVShow.id) > 1
        ).order_by(
            func.count(TVShow.id).desc()
        ).all()

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
    per_page = 30
    query = TVShow.query
    if q:
        try:
            query = query.filter(func.similarity(TVShow.show_name, q) > 0.1).order_by(func.similarity(TVShow.show_name, q).desc())
        except Exception:
            query = query.filter(TVShow.show_name.ilike(f"%{q}%")).order_by(TVShow.created_at.desc())
    else:
        query = query.order_by(TVShow.created_at.desc())

    shows = query.paginate(page=page, per_page=per_page, error_out=False)
    # Pass skipped_files to the template
    return render_template('nuke.html', title="Nuke", shows=shows, q=q, view_dupes=False, skipped_files=skipped_files)

@app.route('/nuke/login', methods=['POST'])
def nuke_login():
    if not _nuke_enabled():
        return render_template('maintenance.html', title="Maintenance"), 503

    ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '0.0.0.0').split(',')[0].strip()
    token = (request.form.get('token') or '').strip()
    if not token:
        return redirect(url_for('nuke_home', msg="Token required"))

    if token != _admin_token():
        r = _redis()
        fk = _fail_key(ip)
        fails = int(r.incr(fk))
        r.expire(fk, 3600)
        if fails >= 2:
            _nuke_disable()
            return redirect(url_for('nuke_home', msg="Locked after 2 failed attempts"))
        return redirect(url_for('nuke_home', msg=f"Invalid token. Attempt {fails}/2"))

    resp = make_response(redirect(url_for('nuke_home')))
    resp.set_cookie('nuke_auth', _cookie_value(), max_age=_nuke_cookie_ttl_days()*24*3600, httponly=True, samesite='Lax', secure=True)
    _redis().delete(_fail_key(ip))
    return resp

@app.route('/nuke/logout', methods=['POST'])
def nuke_logout():
    resp = make_response(redirect(url_for('nuke_home', msg="Logged out")))
    resp.set_cookie('nuke_auth', '', max_age=0)
    return resp

@app.route('/nuke/unlock', methods=['POST'])
def nuke_unlock():
    token = (request.form.get('token') or '').strip()
    if token != _admin_token():
        return redirect(url_for('nuke_home', msg="Wrong key"))
    _nuke_enable()
    return redirect(url_for('nuke_home', msg="Nuke enabled"))

@app.route('/nuke/delete/<int:show_id>', methods=['POST'])
def nuke_delete(show_id):
    if not _is_authed(request):
        return redirect(url_for('nuke_home', msg="Login required"))
    try:
        show = TVShow.query.get_or_404(show_id)
        db.session.delete(show)
        db.session.commit()
        return redirect(f"{url_for('nuke_home')}?{urlencode({'msg': f'Deleted {show.show_name}'})}")
    except Exception as e:
        db.session.rollback()
        logger.error(f"/nuke delete error {show_id}: {e}")
        return redirect(url_for('nuke_home', msg="Delete failed, check logs"))

@app.route('/nuke/bulk-delete', methods=['POST'])
def nuke_bulk_delete():
    if not _is_authed(request):
        return redirect(url_for('nuke_home', msg="Login required"))
    link = (request.form.get('link') or '').strip()
    mode = (request.form.get('mode') or '').strip()
    ids = request.form.getlist('ids')
    try:
        if not link:
            return redirect(url_for('nuke_home', msg="No link provided"))
        if mode == 'selected':
            if not ids:
                return redirect(url_for('nuke_home', dupes=1, msg="No items selected"))
            TVShow.query.filter(TVShow.id.in_(ids), TVShow.download_link == link).delete(synchronize_session=False)
        elif mode == 'all_but_latest':
            items = TVShow.query.filter_by(download_link=link).order_by(TVShow.created_at.desc(), TVShow.id.desc()).all()
            for s in items[1:]:
                db.session.delete(s)
        elif mode == 'all':
            TVShow.query.filter_by(download_link=link).delete(synchronize_session=False)
        else:
            return redirect(url_for('nuke_home', dupes=1, msg="Unknown mode"))
        db.session.commit()
        return redirect(url_for('nuke_home', dupes=1, msg="Bulk delete done"))
    except Exception as e:
        db.session.rollback()
        logger.error(f"/nuke bulk-delete error: {e}")
        return redirect(url_for('nuke_home', dupes=1, msg="Bulk delete failed"))

# --- NEW: Clear Skipped Logs ---
@app.route('/nuke/clear_skipped', methods=['POST'])
def nuke_clear_skipped():
    if not _is_authed(request): return abort(403)
    try:
        db.session.query(SkippedFile).delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return redirect(url_for('nuke_home', msg=f"Error: {e}"))
    return redirect(url_for('nuke_home', msg="Skipped log cleared"))

# --- NEW: Backfill API Control ---
@app.route('/nuke/backfill/<action>', methods=['POST'])
def control_backfill(action):
    if not _is_authed(request): return jsonify({'error': 'Unauthorized'}), 401
    r = _redis()
    
    if action == 'start':
        r.hset("backfill:status", "state", "running")
        # Trigger async task
        celery.send_task('tv_app.tasks.backfill_movies_task')
    elif action == 'pause':
        r.hset("backfill:status", "state", "paused")
    elif action == 'stop':
        r.hset("backfill:status", "state", "stopped")
    elif action == 'reset':
        for k in ["backfill:checkpoint_id", "backfill:added", "backfill:skipped"]:
            r.delete(k)
        r.hmset("backfill:status", {"state": "reset", "progress": 0, "total": 0, "current": "Ready"})
    
    return jsonify({'status': 'ok', 'action': action})

@app.route('/nuke/backfill/status')
def backfill_status():
    if not _is_authed(request): return jsonify({'error': 'Unauthorized'}), 401
    r = _redis()
    status = r.hgetall("backfill:status")
    
    added = r.get("backfill:added") or 0
    skipped = r.get("backfill:skipped") or 0
    
    return jsonify({
        'state': status.get('state', 'idle'),
        'current': status.get('current', '...'),
        'progress': status.get('progress', 0),
        'total': status.get('total', 1),
        'added': str(added),
        'skipped': str(skipped)
    })

# ----------------------------- Health & errors -----------------------------
@app.route('/healthz')
def healthz():
    return jsonify(status="ok", time=datetime.utcnow().isoformat()), 200

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html', title="Page Not Found",
                           meta_description="The page you were looking for could not be found."), 404

@app.errorhandler(500)
def internal_server_error(e):
    try:
        db.session.rollback()
    except Exception as rollback_error:
        logger.error(f"Error during rollback in 500 handler: {rollback_error}")
    return render_template('500.html', title="Internal Server Error",
                           meta_description="We encountered an internal error. Please try again later."), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
