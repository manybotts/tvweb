# =================================================================
# tv_app/tasks.py - PART 1: TEXT CLEANING, REGEX & TMDB FETCHERS
# =================================================================
import os
import re
import time
import asyncio
import logging
import itertools
from typing import Dict, Optional, List
from urllib.parse import quote_plus

import aiohttp
import pymongo
from celery import Celery
from dotenv import load_dotenv
from redis import Redis
from thefuzz import fuzz, process

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

celery = Celery(__name__)
celery.config_from_object("celeryconfig")

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# ---------------- TEXT HELPERS (Harmonized "FBI Fix") ----------------
_ACRONYM_DOTS = re.compile(r"\b([A-Z]\.){2,}\b")
_NON_BASIC = re.compile(r"[^\w\s,&'\-.:]")
_TOK = re.compile(r"[a-z0-9]+")
ARTICLES = {"the", "a", "an"}

def collapse_dotted_acronyms(s: str) -> str:
    def _join(m): return m.group(0).replace(".", "")
    return _ACRONYM_DOTS.sub(_join, s)

def normalize(s: Optional[str]) -> str:
    if not s: return ""
    s = collapse_dotted_acronyms(s)
    s = "".join(c for c in s if c.isprintable())
    s = _NON_BASIC.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()

def tokens(s: str) -> List[str]:
    return _TOK.findall(s.lower())

def strip_leading_article(s: str) -> str:
    toks = tokens(s)
    if toks and toks[0] in ARTICLES:
        return " ".join(toks[1:])
    return " ".join(toks)

def strong_title_score(query: str, candidate: str) -> int:
    """Calculates similarity with length penalty (The 'FBI' Fix)."""
    qn = normalize(query)
    cn = normalize(candidate)
    if qn == cn: return 100
    sq = strip_leading_article(qn)
    sc = strip_leading_article(cn)
    if sq == sc: return 99
    base = fuzz.token_sort_ratio(qn, cn)
    len_q, len_c = len(qn), len(cn)
    if len_q > 0 and len_c > len_q:
        ratio = len_c / len_q
        if ratio > 2.0: base -= 15
        elif ratio > 1.5: base -= 5
    return base

def parse_season_info(line: str) -> Optional[int]:
    nums = re.findall(r"\d+", line)
    return max(int(n) for n in nums) if nums else None

# ---------------- AGGRESSIVE MOVIE CLEANER (V11 Polisher) ----------------
def clean_movie_filename(filename: str):
    if not filename: return None, None
    name = os.path.splitext(filename)[0]
    
    # 1. Handle @ Mentions
    name = re.sub(r"^@\S+\s*", "", name)
    if "@" in name: name = name.split("@")[0]

    # 2. Separators & Brackets
    name = re.sub(r"[._\-]", " ", name)
    name = re.sub(r"[\[\{\(].*?[\]\}\)]", "", name)
    
    # 3. Spam Prefix Nuker
    spam_prefixes = r"^(MLM|SMM|CG|HW|MM|E4E|V|K|Linkz|Video|Infotainment|Sherlibrary|TrollMovies|Mallu Movies|Cinema Villa|Company|All|New|Full Movie)\s+"
    name = re.sub(spam_prefixes, "", name, flags=re.IGNORECASE)
    
    # 4. Domain & Size Removal
    name = re.sub(r"\bwww\s+\S+\s+(ws|com|net|org|in|co|me|win|biz)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"www\.\S+", "", name)
    name = re.sub(r"\b\d+(\s|\.)?\d*\s?(mb|gb)\b", "", name, flags=re.IGNORECASE)

    # 5. Year Extractor
    year_iter = re.finditer(r"\b(19|20)\d{2}\b", name)
    years = [int(m.group(0)) for m in year_iter]
    year = years[0] if years else None
    
    raw_title = name
    if year:
        match = re.search(r"\b" + str(year) + r"\b", name)
        if match:
            pre_year, post_year = name[:match.start()].strip(), name[match.end():].strip()
            raw_title = pre_year if len(pre_year) > 2 else (post_year if len(post_year) > 2 else name)

    # 6. Quality/Codec Safe Kill List
    stop_words = {"1080p", "720p", "480p", "2160p", "4k", "bluray", "webrip", "dvdrip", "x264", "x265", "hevc", "aac", "hindi", "english"}
    clean_tokens = []
    for t in raw_title.split():
        t_clean = re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", t)
        if not t_clean or t_clean.lower() in stop_words: break
        clean_tokens.append(t_clean)
            
    final_title = " ".join(clean_tokens)
    return (final_title, year) if len(final_title) >= 2 else (None, None)

# ---------------- TMDB FETCHERS ----------------
async def fetch_tmdb_data(show_name: str, search_year: Optional[int], search_season: Optional[int]) -> Optional[Dict]:
    """Source of Truth: TV Show Lookup."""
    tmdb_bearer_token = os.environ.get("TMDB_BEARER_TOKEN")
    headers = {"Authorization": f"Bearer {tmdb_bearer_token}"}
    q_name = show_name.strip()
    async with aiohttp.ClientSession(headers=headers) as session:
        url = f"{TMDB_BASE_URL}/search/tv?query={quote_plus(q_name)}&language=en-US"
        try:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
        except: return None
        if not data.get("results"): return None
        detailed = []
        for r in data["results"]:
            try:
                async with session.get(f"{TMDB_BASE_URL}/tv/{r['id']}?language=en-US", timeout=5) as d:
                    if d.status == 200: detailed.append(await d.json())
            except: continue
        if not detailed: return None
        best = (None, -1)
        for r in detailed:
            s = max(strong_title_score(q_name, r.get("name", "")), strong_title_score(q_name, r.get("original_name", "")))
            fa = r.get("first_air_date", "")
            if search_year and fa[:4] == str(search_year): s += 10
            if search_season and int(r.get("number_of_seasons", 0)) >= search_season: s += 2
            if s > best[1]: best = (r, s)
        found = best[0]
        if not found or best[1] < 50: return None
        return {
            "tmdb_id": found.get("id"), "show_name_from_tmdb": found.get("name"),
            "poster_path": f"{TMDB_IMAGE_BASE_URL}{found.get('poster_path')}" if found.get("poster_path") else None,
            "overview": found.get("overview"), "vote_average": found.get("vote_average"),
            "year": int(found.get("first_air_date")[:4]) if found.get("first_air_date") else None,
            "rating": found.get("vote_average")
        }

async def fetch_movie_data(title: str, year: Optional[int]) -> Optional[Dict]:
    """Movie Logic with Tiered Retry."""
    tmdb_bearer_token = os.environ.get("TMDB_BEARER_TOKEN")
    headers = {"Authorization": f"Bearer {tmdb_bearer_token}"}
    queries = [title]
    words = title.split()
    if len(words) > 1: queries.append(" ".join(words[:-1]))
    
    async with aiohttp.ClientSession(headers=headers) as session:
        for q in queries:
            try:
                url = f"{TMDB_BASE_URL}/search/movie?query={quote_plus(q)}&language=en-US"
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                if not data.get("results"): continue
                best = None
                for r in data["results"]:
                    r_yr = int(r.get("release_date")[:4]) if r.get("release_date") else None
                    if year and r_yr == year: best = r; break
                if not best and strong_title_score(q, data["results"][0]['title']) > 70:
                    best = data["results"][0]
                if best:
                    return {
                        "tmdb_id": best.get("id"), "show_name_from_tmdb": best.get("title"),
                        "poster_path": best.get('poster_path'), "overview": best.get("overview"),
                        "vote_average": best.get("vote_average"), "rating": best.get("vote_average"),
                        "year": int(best.get("release_date")[:4]) if best.get("release_date") else None
                    }
            except: pass
    return None

# =================================================================
# END OF PART 1 - CLEANERS & FETCHERS
# =================================================================
# =================================================================
# tv_app/tasks.py - PART 2: INGESTION, BACKFILL & MAINTENANCE
# =================================================================

def generate_search_link(title: str) -> str:
    """Generates a deep-link for the Telegram Bot Handshake."""
    bot = os.environ.get("BOT_USERNAME", "YourBot")
    return f"https://t.me/{bot}?start=search_{quote_plus(title)}"

# --------------- TELEGRAM INGEST (Source of Truth) ----------------
async def fetch_new_telegram_posts(channel_env_var: str, redis_key_suffix: str) -> list:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get(channel_env_var)
    if not channel_id: return []

    redis_client = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    last_offset_key = f"last_telegram_update_id:{redis_key_suffix}"
    last_offset = int(redis_client.get(last_offset_key) or 0)

    from telegram.ext import Application
    try:
        app = Application.builder().token(token).build()
        updates = await app.bot.get_updates(offset=last_offset + 1, allowed_updates=["channel_post", "edited_channel_post"], timeout=60)
        await app.shutdown()
        posts = [u.channel_post or u.edited_channel_post for u in updates if (u.channel_post or u.edited_channel_post) and str((u.channel_post or u.edited_channel_post).sender_chat.id) == channel_id]
        if updates: redis_client.set(last_offset_key, updates[-1].update_id)
        return posts
    except Exception as e:
        logger.error(f"Telegram fetch error: {e}")
        return []

def parse_telegram_post(post) -> Optional[Dict]:
    """Source of Truth: Aggressive Year Stripping logic."""
    try:
        text = post.caption or ""
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2: return None
        
        title_line = lines[0]
        season_line = lines[1]
        
        clean_line = re.sub(r"[\[\]\(\)]", " ", title_line)
        norm_title = normalize(clean_line)
        year_match = re.search(r"(\d{4})$", norm_title)
        
        search_year = int(year_match.group(1)) if year_match else None
        show_name = re.sub(r"\s*\d{4}$", "", norm_title).strip() if year_match else norm_title
        
        download_link = None
        if post.caption_entities:
            for ent in post.caption_entities:
                if ent.type == "text_link" and "click here" in text[ent.offset: ent.offset + ent.length].lower():
                    download_link = ent.url; break
        if not download_link:
            for ln in reversed(lines):
                if "#_" in ln: continue
                m = re.search(r"(https?://\S+)", ln)
                if m: download_link = m.group(1); break
        
        return {
            "show_name_for_search": show_name or norm_title,
            "search_year": search_year,
            "search_season": parse_season_info(season_line),
            "season_episode_from_post": season_line,
            "download_link_from_post": download_link,
            "message_id": int(post.message_id)
        }
    except: return None

@celery.task(bind=True, retry_backoff=True, max_retries=3)
def update_tv_shows(self):
    """Main task for TV and Anime ingestion from Telegram."""
    redis_client = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=120)
    if not lock.acquire(blocking=False): return
    from tv_app.app import app
    with app.app_context():
        try:
            from tv_app.models import db, TVShow
            sources = [{'type': 'tv', 'env': 'TELEGRAM_CHANNEL_ID', 'offset': 'tv_main'}, {'type': 'anime', 'env': 'TELEGRAM_ANIME_CHANNEL_ID', 'offset': 'anime_main'}]
            for src in sources:
                posts = asyncio.run(fetch_new_telegram_posts(src['env'], src['offset']))
                for post in posts:
                    if redis_client.exists(f"processed_messages:{post.message_id}"): continue
                    p = parse_telegram_post(post)
                    if not p: continue
                    tmdb = asyncio.run(fetch_tmdb_data(p["show_name_for_search"], p["search_year"], p["search_season"]))
                    if not tmdb: continue
                    
                    existing = TVShow.query.filter_by(tmdb_id=tmdb["tmdb_id"], category=src['type']).first()
                    if existing: db.session.delete(existing); db.session.flush()
                    
                    db.session.add(TVShow(
                        tmdb_id=tmdb["tmdb_id"], message_id=p["message_id"], show_name=tmdb["show_name_from_tmdb"],
                        episode_title=p["season_episode_from_post"], download_link=p["download_link_from_post"],
                        poster_path=tmdb["poster_path"], overview=tmdb["overview"], vote_average=tmdb["vote_average"],
                        year=tmdb["year"], rating=tmdb["rating"], category=src['type'],
                        content_hash=f"{tmdb['tmdb_id']}-{p['season_episode_from_post']}"
                    ))
                    redis_client.set(f"processed_messages:{post.message_id}", 1, ex=86400)
            db.session.commit()
        except Exception as e:
            logger.error(f"Task error: {e}"); db.session.rollback()
        finally: lock.release()

# --------------- MOVIE BACKFILL ENGINE (Harmonized) ----------------
@celery.task(bind=True)
def backfill_movies_task(self):
    """Heavy-duty MongoDB backfill with Redis checkpoints."""
    r = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    last_id = r.get("backfill:checkpoint_id")
    r.hmset("backfill:status", {"state": "running", "current": "Initializing..."})
    
    try:
        uris = [u for u in [os.environ.get("MONGO_URI_1"), os.environ.get("MONGO_URI_2")] if u]
        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow, SkippedFile
            
            def is_obvious_tv(name):
                return re.search(r"\b(S\d+E\d+|Episode|Season)\b", name, re.I)

            for uri in uris:
                client = pymongo.MongoClient(uri)
                col = client[os.environ.get("MONGO_DB_NAME", "db")][os.environ.get("MONGO_COL_NAME", "col")]
                query = {"file_size": {"$gt": 314572800}} # 300MB min
                if last_id: query["_id"] = {"$lt": last_id}
                
                cursor = col.find(query).sort("_id", -1)
                for doc in cursor:
                    # Check for Pause/Stop signals
                    state = r.hget("backfill:status", "state")
                    if state == "stopped": return
                    while state == "paused": time.sleep(2); state = r.hget("backfill:status", "state")

                    cur_id, raw = doc.get('_id'), doc.get('file_name', '')
                    r.hset("backfill:status", "current", raw[:30])
                    
                    if is_obvious_tv(raw):
                        r.set("backfill:checkpoint_id", str(cur_id)); continue

                    clean, year = clean_movie_filename(raw)
                    if not clean:
                        if not SkippedFile.query.filter_by(filename=raw).first():
                            db.session.add(SkippedFile(filename=raw, reason="Cleaner Failed"))
                            db.session.commit()
                        r.set("backfill:checkpoint_id", str(cur_id)); continue

                    # Skip if exists
                    if TVShow.query.filter_by(show_name=clean, category='movie').first():
                        r.set("backfill:checkpoint_id", str(cur_id)); continue

                    time.sleep(0.3) # Rate limit
                    meta = asyncio.run(fetch_movie_data(clean, year))
                    
                    if meta:
                        db.session.add(TVShow(
                            tmdb_id=meta["tmdb_id"], message_id=0, show_name=meta["show_name_from_tmdb"],
                            episode_title="Full Movie", download_link=generate_search_link(meta["show_name_from_tmdb"]),
                            poster_path=meta["poster_path"], overview=meta["overview"], 
                            vote_average=meta["vote_average"], year=meta["year"], rating=meta["rating"],
                            category='movie', content_hash=f"movie_{meta['tmdb_id']}"
                        ))
                        db.session.commit(); r.incr("backfill:added")
                    else:
                        if not SkippedFile.query.filter_by(filename=raw).first():
                            db.session.add(SkippedFile(filename=raw, reason=f"TMDB No Result: {clean}"))
                            db.session.commit()
                        r.incr("backfill:skipped")
                    
                    r.set("backfill:checkpoint_id", str(cur_id))
    except Exception as e:
        r.hset("backfill:status", "state", f"Error: {e}")
    r.hset("backfill:status", "state", "complete")

@celery.task(name="tv_app.tasks.reset_clicks")
def reset_clicks():
    from tv_app.app import app
    with app.app_context():
        from tv_app.models import db, TVShow
        TVShow.query.update({TVShow.clicks: 0}); db.session.commit()

@celery.task(name="tv_app.tasks.test_task")
def test_task(): return "OK"
