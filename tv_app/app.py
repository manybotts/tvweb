# tv_app/app.py (slug primary + SEO + Nuke + bulk delete)
import os, re, random, time, logging
from datetime import datetime, date, timedelta
from urllib.parse import urlparse
from dotenv import load_dotenv
from flask import Flask, render_template, redirect, url_for, request, jsonify, Response, abort, flash, session
from sqlalchemy import func, or_
from redis import Redis
from werkzeug.utils import secure_filename
from .models import db, TVShow, Genre

load_dotenv(dotenv_path="/root/tvweb/.env")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "your_secret_key")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///tv_shows.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def _slugify(text: str) -> str:
    s = secure_filename(text or "").replace("_", "-").lower()
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "item"

@app.context_processor
def inject_now(): return {"now": datetime.utcnow}

@app.template_filter("hostonly")
def hostonly(url: str) -> str:
    try: return urlparse(url).netloc or ""
    except Exception: return ""

def get_trending_shows(limit: int = 6):
    with app.app_context():
        return TVShow.query.order_by(TVShow.clicks.desc()).limit(limit).all()

# ---------------- public
@app.route("/")
def index():
    search_query = (request.args.get("search") or "").strip()
    page = request.args.get("page", 1, type=int); per_page = 10
    trending = get_trending_shows(); message = None
    if search_query:
        try:
            sim = 0.1
            shows = (TVShow.query.filter(func.similarity(TVShow.show_name, search_query) > sim)
                     .order_by(func.similarity(TVShow.show_name, search_query).desc())
                     .paginate(page=page, per_page=per_page, error_out=False))
            if not shows.items:
                shows = (TVShow.query.filter(TVShow.show_name.ilike(f"%{search_query}%"))
                         .order_by(TVShow.created_at.desc())
                         .paginate(page=page, per_page=per_page, error_out=False))
                if not shows.items:
                    shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
                    message = f"No matches found for '{search_query}'. Showing most recent additions."
                    return render_template("index.html", shows=shows, search_query=search_query, trending_shows=[], message=message, title=f"No Results for '{search_query}'",
                                           canonical_url=url_for("index", search=search_query, _external=True), meta_robots="noindex,follow",
                                           prev_url=None, next_url=(url_for("index", search=search_query, page=page+1, _external=True) if shows.has_next else None))
        except Exception as e:
            logger.error(f"Search error: {e}")
            shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
            return render_template("index.html", shows=shows, search_query=search_query, trending_shows=trending, message="An error occurred during search.", title="Search Error",
                                   canonical_url=url_for("index", search=search_query, _external=True), meta_robots="noindex,follow",
                                   prev_url=(url_for("index", search=search_query, page=page-1, _external=True) if shows.has_prev else None),
                                   next_url=(url_for("index", search=search_query, page=page+1, _external=True) if shows.has_next else None))
    else:
        shows = TVShow.query.order_by(TVShow.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    def _p(p): return url_for("index", page=p, search=(search_query or None), _external=True)
    prev_url = _p(page-1) if shows.has_prev else None
    next_url = _p(page+1) if shows.has_next else None
    canonical_url = (url_for("index", search=search_query, _external=True) if search_query else _p(page))
    meta_robots = ("noindex,follow" if search_query else None)
    page_title = f"Search Results: {search_query}" if search_query else "Search & Download Latest TV Shows"
    return render_template("index.html", shows=shows, search_query=search_query, trending_shows=trending, message=message, title=page_title,
                           canonical_url=canonical_url, meta_robots=meta_robots, prev_url=prev_url, next_url=next_url)

# Primary: /show/<slug>
@app.route("/show/<string:slug>")
def show_details(slug):
    s = TVShow.query.filter_by(slug=slug).first_or_404()
    try:
        s.clicks = (s.clicks or 0) + 1; db.session.commit()
    except Exception:
        db.session.rollback()
    parts = [s.show_name]; 
    if s.episode_title: parts.append(s.episode_title)
    parts.append("Details & Download"); title = " - ".join(parts)
    if s.overview:
        desc = s.overview[:155] + "..." if len(s.overview) > 155 else s.overview
        meta = f"{desc} Find details and download link on iBOX TV."
    else:
        meta = f"View details and download {s.show_name}{' - ' + s.episode_title if s.episode_title else ''} on iBOX TV."
    canonical = url_for("show_details", slug=s.slug, _external=True)
    return render_template("show_details.html", show=s, title=title, meta_description=meta[:160], canonical_url=canonical)

# Legacy: /show/<id> -> 301 to slug (kept for safety)
@app.route("/show/<int:show_id>")
def show_details_id(show_id):
    s = TVShow.query.get_or_404(show_id)
    return redirect(url_for("show_details", slug=s.slug), code=301)

@app.route("/redirect/<int:show_id>")
def redirect_to_download(show_id):
    s = TVShow.query.get(show_id)
    if not s:
        logger.info("redirect: show %s not found", show_id)
        return render_template("404.html", title="Not Found", meta_description="Link expired or post deleted."), 404
    if not s.download_link:
        logger.info("redirect: no link for show %s", show_id)
        return render_template("404.html", title="Not Found", meta_description="Download link not available."), 404
    return redirect(s.download_link)

@app.route("/shows")
def list_shows():
    try:
        page = request.args.get("page", 1, type=int); per = 30
        genre_filter = request.args.get("genre"); rf = request.args.get("rating", type=int); yf = request.args.get("year", type=int)
        sort_by = request.args.get("sort_by", "name_asc")
        q = TVShow.query
        if genre_filter: q = q.join(TVShow.genres).filter(Genre.name == genre_filter)
        if yf: q = q.filter(TVShow.year == yf)
        if rf is not None:
            lb = float(rf); q = q.filter(TVShow.rating >= lb) if rf == 10 else q.filter(TVShow.rating >= lb, TVShow.rating < lb + 1.0)
        if sort_by == "name_asc": q = q.order_by(TVShow.show_name.asc())
        elif sort_by == "name_desc": q = q.order_by(TVShow.show_name.desc())
        elif sort_by == "date_asc": q = q.order_by(TVShow.created_at.asc())
        elif sort_by == "date_desc": q = q.order_by(TVShow.created_at.desc())
        elif sort_by == "rating_asc": q = q.order_by(TVShow.rating.asc().nullslast())
        elif sort_by == "rating_desc": q = q.order_by(TVShow.rating.desc().nullslast())
        shows = q.paginate(page=page, per_page=per, error_out=False)
        all_genres = Genre.query.order_by(Genre.name).all()
        cy = datetime.now().year
        mn = db.session.query(func.min(TVShow.year)).filter(TVShow.year.isnot(None)).scalar()
        years = list(range(cy, (mn if mn is not None else cy - 20) - 1, -1))
        ratings = list(range(10, -1, -1))

        def _p(p):
            args = dict(page=p, sort_by=sort_by)
            if genre_filter: args["genre"] = genre_filter
            if rf is not None: args["rating"] = rf
            if yf: args["year"] = yf
            return url_for("list_shows", _external=True, **args)

        prev_url = _p(page-1) if shows.has_prev else None
        next_url = _p(page+1) if shows.has_next else None
        canonical_url = _p(page)
        return render_template("shows.html", shows=shows, genres=all_genres, ratings=ratings, years=years,
                               selected_genre=genre_filter, selected_rating=rf, selected_year=yf, current_sort_by=sort_by,
                               title="Available TV Shows", canonical_url=canonical_url, meta_robots=None,
                               prev_url=prev_url, next_url=next_url)
    except Exception as e:
        logger.error(f"list_shows error: {e}"); db.session.rollback()
        return render_template("500.html", title="Server Error", meta_description="An error occurred."), 500

@app.route("/privacy-policy")
def privacy_policy(): return render_template("privacy_policy.html", title="Privacy Policy")

@app.route("/update", methods=["POST"])
def update():
    try:
        from .tasks import update_tv_shows
        update_tv_shows.delay(); return jsonify({"message": "Update initiated"}), 202
    except Exception as e:
        logger.error(f"update trigger error: {e}"); return jsonify({"message": "Error initiating update"}), 500

@app.route("/test_celery")
def test_celery():
    try:
        from .tasks import test_task
        r = test_task.delay(); return f"Celery test task initiated. Task ID: {r.id}", 200
    except Exception as e:
        logger.error(f"test_celery error: {e}"); return jsonify({"message": "Error initiating test task"}), 500

@app.route("/delete_all", methods=["POST"])
def delete_all_shows():
    try:
        n = db.session.query(TVShow).delete(); db.session.commit()
        logger.info(f"Deleted {n} shows."); return jsonify({"message": f"All {n} shows deleted."}), 200
    except Exception as e:
        db.session.rollback(); logger.error(f"delete_all error: {e}")
        return jsonify({"message": f"Error deleting shows: {e}"}), 500

@app.route("/ads.txt")
def ads_txt_redirect(): return redirect("https://srv.adstxtmanager.com/75094/ibox-tv.com", code=301)

# ---------------- nuke
NUKE_ENABLED_KEY="nuke:enabled"; NUKE_FAIL_KEY="nuke:fail:global"; NUKE_MAX_GLOBAL_FAILS=2
def _r():
    url = os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0"
    return Redis.from_url(url, decode_responses=True)
def _nuke_enabled(): return (_r().get(NUKE_ENABLED_KEY) in (None, "1"))
def _disable_nuke(): _r().set(NUKE_ENABLED_KEY,"0")
def _enable_nuke(): _r().set(NUKE_ENABLED_KEY,"1"); _r().delete(NUKE_FAIL_KEY)
def _record_fail(): n=_r().incr(NUKE_FAIL_KEY); _r().expire(NUKE_FAIL_KEY,900); return n
def _submitted_key(req):
    return (req.headers.get("X-Admin-Token") or req.headers.get("X-Nuke-Token")
            or req.form.get("key") or req.args.get("key") or req.args.get("admin")
            or req.form.get("admin_token") or "").strip()
def _admin_ok(req):
    if session.get("nuke_auth") is True: return True
    token=os.environ.get("ADMIN_TOKEN") or os.environ.get("SECRET_KEY")
    cand=_submitted_key(req)
    if token and cand and cand==token:
        session["nuke_auth"]=True; session.permanent=True; _r().delete(NUKE_FAIL_KEY); return True
    return False
def _get_show(i:int):
    try: return db.session.get(TVShow,i)
    except Exception: return TVShow.query.get(i)

@app.route("/nuke/healthz")
def nuke_healthz():
    try:
        enabled = 1 if _nuke_enabled() else 0
        authed = 1 if session.get("nuke_auth") else 0
        return jsonify({"enabled": enabled, "auth": authed}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/nuke", methods=["GET","POST"])
def nuke_panel():
    token=os.environ.get("ADMIN_TOKEN") or os.environ.get("SECRET_KEY")
    cand=_submitted_key(request)
    if not _nuke_enabled():
        if request.method=="POST" and token and cand==token:
            _enable_nuke(); session["nuke_auth"]=True; session.permanent=True
            return redirect(url_for("nuke_panel"))
        return abort(404)
    if not _admin_ok(request):
        if request.method=="POST":
            time.sleep(random.uniform(0.25,0.6))
            if _record_fail()>=NUKE_MAX_GLOBAL_FAILS: _disable_nuke(); return abort(404)
            left=NUKE_MAX_GLOBAL_FAILS-int(_r().get(NUKE_FAIL_KEY) or 0)
            return render_template("nuke_login.html", error=f"Invalid key. {left} attempt{'s' if left!=1 else ''} left.")
        return render_template("nuke_login.html", error=None)
    q=(request.args.get("q") or "").strip()
    dupes_param=request.args.get("dupes", type=int)
    view_dupes=(dupes_param==1) or (not q and dupes_param is None)
    if view_dupes:
        sub=(db.session.query(TVShow.download_link)
             .filter(TVShow.download_link.isnot(None), TVShow.download_link!="")
             .group_by(TVShow.download_link).having(func.count(TVShow.id)>1))
        base=TVShow.query.filter(TVShow.download_link.in_(sub))
        if q:
            like=f"%{q}%"
            base=base.filter(or_(TVShow.show_name.ilike(like), TVShow.episode_title.ilike(like), TVShow.download_link.ilike(like)))
        rows=base.order_by(TVShow.created_at.desc()).limit(2000).all()
        groups={}
        for s in rows: groups.setdefault(s.download_link, []).append(s)
        dupe_groups=[{"link":k,"domain":urlparse(k).netloc or "","shows":v} for k,v in groups.items()]
        dupe_groups.sort(key=lambda g:(-len(g["shows"]), g["domain"]))
        return render_template("nuke.html", q=q, shows=[], view_dupes=True, dupe_groups=dupe_groups)
    shows=[]
    if q:
        if q.startswith("id:") and q[3:].isdigit():
            one=_get_show(int(q[3:])); shows=[one] if one else []
        else:
            like=f"%{q}%"
            shows=(TVShow.query.filter(or_(TVShow.show_name.ilike(like), TVShow.episode_title.ilike(like), TVShow.download_link.ilike(like)))
                   .order_by(TVShow.created_at.desc()).limit(200).all())
    return render_template("nuke.html", q=q, shows=shows, view_dupes=False, dupe_groups=[])

@app.route("/nuke/logout", methods=["POST","GET"])
def nuke_logout():
    session.pop("nuke_auth", None); flash("Nuke access cleared.","info")
    return redirect(url_for("nuke_panel"))

@app.route("/nuke/delete/<int:show_id>", methods=["POST"])
def nuke_delete(show_id:int):
    if not _nuke_enabled(): return abort(404)
    if not _admin_ok(request): return ("Forbidden",403)
    s=_get_show(show_id)
    if not s: flash("Post not found.","warning"); return redirect(url_for("nuke_panel", q=request.args.get("q")))
    try:
        if hasattr(s,"genres"): s.genres=[]
        db.session.delete(s); db.session.commit(); flash(f"Deleted post #{show_id}.","success")
    except Exception as e:
        db.session.rollback(); flash(f"Delete failed: {e}","danger")
    return redirect(url_for("nuke_panel", q=request.args.get("q")))

@app.route("/nuke/bulk_delete", methods=["POST"])
def nuke_bulk_delete():
    if not _nuke_enabled(): return abort(404)
    if not _admin_ok(request): return ("Forbidden", 403)
    q = (request.args.get("q") or request.form.get("q") or "").strip()
    mode = (request.form.get("mode") or "selected").strip()
    link = (request.form.get("link") or "").strip()
    ids = [int(x) for x in request.form.getlist("ids") if (x or "").isdigit()]
    try:
        if mode == "selected":
            if not ids: flash("No items selected.","warning"); return redirect(url_for("nuke_panel", q=q, dupes=1))
            shows = TVShow.query.filter(TVShow.id.in_(ids)).all()
        elif mode == "all":
            if not link: flash("Missing link.","danger"); return redirect(url_for("nuke_panel", q=q, dupes=1))
            shows = TVShow.query.filter(TVShow.download_link == link).all()
        elif mode == "all_but_latest":
            if not link: flash("Missing link.","danger"); return redirect(url_for("nuke_panel", q=q, dupes=1))
            rows = TVShow.query.filter(TVShow.download_link == link).order_by(TVShow.created_at.desc()).all()
            keep = rows[0].id if rows else None
            shows = [s for s in rows if s.id != keep]
        else:
            flash("Unknown bulk mode.","danger"); return redirect(url_for("nuke_panel", q=q, dupes=1))
        cnt = 0
        for s in shows:
            try:
                if hasattr(s,"genres"): s.genres=[]
                db.session.delete(s); cnt += 1
            except Exception: pass
        db.session.commit(); flash(f"Bulk delete: removed {cnt} item(s).","success")
    except Exception as e:
        db.session.rollback(); flash(f"Bulk delete failed: {e}","danger")
    return redirect(url_for("nuke_panel", q=q, dupes=1))

# ---------------- seo
def _abs_url(endpoint:str,**v): 
    base=os.environ.get("SITE_BASE_URL")
    if not base: return url_for(endpoint, _external=True, **v)
    return base.rstrip("/") + url_for(endpoint, _external=False, **v)

def _fmt_lastmod(dt): 
    d = dt.date() if isinstance(dt, datetime) else dt or date.today()
    return d.isoformat()

@app.route("/sitemap.xml")
def sitemap_xml():
    stat=[(_abs_url("index"),date.today()),(_abs_url("shows"),date.today())]
    items=(db.session.query(TVShow).order_by(TVShow.created_at.desc()).limit(5000).all())
    urls=list(stat)
    for s in items:
        try: loc=_abs_url("show_details", slug=s.slug)
        except Exception: continue
        last=getattr(s,"updated_at",None) or getattr(s,"created_at",None) or date.today()
        urls.append((loc,last))
    parts=['<?xml version="1.0" encoding="UTF-8"?>','<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc,last in urls:
        pr="1.0" if loc.endswith("/") else ("0.8" if loc.endswith("/shows") else "0.6")
        parts+=["  <url>",f"    <loc>{loc}</loc>",f"    <lastmod>{_fmt_lastmod(last)}</lastmod>","    <changefreq>daily</changefreq>",f"    <priority>{pr}</priority>","  </url>"]
    parts.append("</urlset>\n")
    return Response("\n".join(parts), mimetype="application/xml")

@app.route("/robots.txt")
def robots_txt():
    try: sm=_abs_url("sitemap_xml")
    except Exception: sm=""
    lines=["User-agent: *","Disallow: /admin"]
    if sm: lines.append(f"Sitemap: {sm}")
    return Response("\n".join(lines)+"\n", mimetype="text/plain")

@app.route("/admin", methods=["GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS"])
@app.route("/admin/<path:_r>", methods=["GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS"])
def shadow_admin(_r=None): abort(404)

@app.errorhandler(404)
def not_found(e): return render_template("404.html", title="Page Not Found", meta_description="The page you were looking for could not be found."), 404

@app.errorhandler(500)
def err_500(e):
    try: db.session.rollback()
    except Exception as x: logger.error(f"rollback error: {x}")
    return render_template("500.html", title="Internal Server Error", meta_description="We encountered an internal error. Please try again later."), 500
