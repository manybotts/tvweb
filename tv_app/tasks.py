# --- tv_app/tasks.py (PART 1) ---
# V11: Spam Prefix Nuker, Size Cleaner, Space Fixer, Safe Kill List
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

# ---------------- TEXT HELPERS ----------------
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
    qn = normalize(query)
    cn = normalize(candidate)
    if qn == cn: return 100
    sq = strip_leading_article(qn)
    sc = strip_leading_article(cn)
    if sq == sc: return 99
    base = fuzz.token_sort_ratio(qn, cn)
    len_q = len(qn)
    len_c = len(cn)
    if len_q > 0 and len_c > len_q:
        ratio = len_c / len_q
        if ratio > 2.0: base -= 15
        elif ratio > 1.5: base -= 5
    return base

def parse_season_info(line: str) -> Optional[int]:
    nums = re.findall(r"\d+", line)
    return max(int(n) for n in nums) if nums else None

# ---------------- AGGRESSIVE MOVIE CLEANER (V11 - The Polisher) ----------------
def clean_movie_filename(filename: str):
    if not filename: return None, None
    
    name = os.path.splitext(filename)[0]
    
    # 1. Handle Leading/Trailing @ Mentions
    name = re.sub(r"^@\S+\s*", "", name) # Leading
    if "@" in name: name = name.split("@")[0] # Trailing

    if not name.strip(): return None, None

    # 2. SEPARATORS & BRACKETS (Handle {}, [], ())
    name = re.sub(r"[._\-]", " ", name)
    name = re.sub(r"\[.*?\]", "", name)
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"\{.*?\}", "", name)
    
    # 3. SPAM PREFIX NUKER
    # Remove specific prefixes if they appear at start of line
    spam_prefixes = r"^(MLM|SMM|CG|HW|MM|E4E|V|K|Linkz|Video|Infotainment|Sherlibrary|TrollMovies|Mallu Movies|Cinema Villa|Company|All|New|Full Movie)\s+"
    name = re.sub(spam_prefixes, "", name, flags=re.IGNORECASE)
    
    # Remove Domains
    name = re.sub(r"\bwww\s+\S+\s+(ws|com|net|org|in|co|me|win|biz)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\bwww\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"www\.\S+", "", name)

    # 4. FIX CONJOINED JUNK & SIZES
    # "2015HDRip" -> "2015 HDRip"
    name = re.sub(r"(\d{4})([A-Z])", r"\1 \2", name)
    # Remove file sizes: "350MB", "1.2GB", "1 2GB"
    name = re.sub(r"\b\d+(\s|\.)?\d*\s?(mb|gb)\b", "", name, flags=re.IGNORECASE)

    # 5. Extract Year
    year_iter = re.finditer(r"\b(19|20)\d{2}\b", name)
    years = [int(m.group(0)) for m in year_iter]
    year = years[0] if years else None
    
    raw_title = name
    if year:
        match = re.search(r"\b" + str(year) + r"\b", name)
        if match:
            pre_year = name[:match.start()].strip()
            post_year = name[match.end():].strip()
            # Prefer text before year unless it's empty
            if len(pre_year) > 2:
                raw_title = pre_year
            elif len(post_year) > 2:
                raw_title = post_year

    # 6. THE SAFE KILL LIST
    stop_words = {
        # Qualities
        "1080p", "720p", "480p", "2160p", "4k", "5k", "8k", "hd", "fhd", "hdtv", "uhd", "sdr", "hq", "sdtv",
        "bluray", "webrip", "web-dl", "dvdrip", "camrip", "hdrip", "hdcam", "bdrip", "brrip",
        "hdtvrip", "web", "dvd", "cam", "ts", "tc", "scr", "dvdscreener", "remux", "internal",
        # Codecs
        "x264", "x265", "hevc", "h264", "h265", "avc", "vc1", "vp9", "divx", "xvid",
        "10bit", "8bit", "10-bit", "8-bit", "hdr", "hdr10", "hdr10+", "dv", "dolby", "vision",
        # Audio & Subs
        "aac", "ac3", "dts", "dts-hd", "truehd", "atmos", "dd5.1", "dd+", "flac", "mp3", "opus",
        "eac3", "2ch", "6ch", "5.1", "7.1", "dual", "audio", "multi", "dub", "sub", "eng",
        "hc", "esub", "esubs", "hardsub", "softsub",
        # Formats/Containers
        "avi", "mkv", "mp4", "zip", "rar", "7z", "iso", "srt", "ass",
        # Groups/Encoders
        "psa", "megusta", "rmteam", "rarbg", "yify", "yts", "tgx", "galaxyrg", "evo", "etrg",
        "mzabi", "judas", "qxr", "tigole", "joy", "utr", "ion10", "pahe", "gaz", "ytsmx", 
        "fgt", "flux", "geckos", "amiable", "sparks", "drones", "vxt", "strife", "veto", "p2p",
        # Technical Junk
        "cc", "kontrakt", "uploaded", "gallery", "ws", "org", "com", "win", "net", "in", "zone",
        "company", "infotainment", "proper", "repack", "untouched", "restored", "criterion",
        # Languages
        "hindi", "english", "malayalam", "tamil", "telugu", "kannada", "japanese", "korean", "chinese", "punjabi"
    }
    
    clean_tokens = []
    for t in raw_title.split():
        # Trim non-alphanumeric edges
        t_clean = re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", t)
        if not t_clean: continue
        
        if t_clean.lower() in stop_words:
            break
            
        clean_tokens.append(t_clean)
            
    final_title = " ".join(clean_tokens)
    
    if len(final_title) < 2: return None, None
        
    return final_title, year

def generate_search_link(title: str) -> str:
    bot = os.environ.get("BOT_USERNAME", "YourBot")
    return f"https://t.me/{bot}?start=search_{quote_plus(title)}"

# ---------------- TMDB FETCHERS ----------------
async def fetch_tmdb_data(show_name: str, search_year: Optional[int], search_season: Optional[int]) -> Optional[Dict]:
    """TV Show Logic"""
    tmdb_bearer_token = os.environ.get("TMDB_BEARER_TOKEN")
    headers = {"Authorization": f"Bearer {tmdb_bearer_token}"}
    q_name = show_name.strip()
    async with aiohttp.ClientSession(headers=headers) as session:
        search_url = f"{TMDB_BASE_URL}/search/tv?query={quote_plus(q_name)}&language=en-US"
        try:
            async with session.get(search_url, timeout=10) as resp:
                resp.raise_for_status()
                search_data = await resp.json()
        except Exception: return None
        if not search_data.get("results"): return None
        detailed = []
        for r in search_data["results"]:
            try:
                async with session.get(f"{TMDB_BASE_URL}/tv/{r['id']}?language=en-US", timeout=5) as d:
                    if d.status == 200: detailed.append(await d.json())
            except Exception: continue
        if not detailed: return None
        best = (None, -1)
        qn = normalize(q_name)
        for r in detailed:
            name = r.get("name") or ""
            oname = r.get("original_name") or ""
            s = max(strong_title_score(q_name, name), strong_title_score(q_name, oname))
            fa = r.get("first_air_date") or ""
            if search_year and fa[:4] == str(search_year): s += 10
            if search_season:
                sc = int(r.get("number_of_seasons") or 0)
                if sc >= search_season: s += max(0, 6 - abs(sc - search_season))
            if s > best[1]: best = (r, s)
        found = best[0]
        if not found or best[1] < 50:
            pick = process.extractOne(qn, [r.get("name") for r in detailed], scorer=fuzz.token_set_ratio)
            if pick:
                for r in detailed:
                    if r.get("name") == pick[0]: found = r; break
        if not found: return None
        return {
            "tmdb_id": found.get("id"), "show_name_from_tmdb": found.get("name"),
            "poster_path": f"{TMDB_IMAGE_BASE_URL}{found.get('poster_path')}" if found.get("poster_path") else None,
            "overview": found.get("overview"), "vote_average": found.get("vote_average"),
            "year": int(found.get("first_air_date")[:4]) if found.get("first_air_date") else None,
            "rating": found.get("vote_average")
        }

async def fetch_movie_data(title: str, year: Optional[int]) -> Optional[Dict]:
    """Movie Logic with TIERED RETRY."""
    tmdb_bearer_token = os.environ.get("TMDB_BEARER_TOKEN")
    headers = {"Authorization": f"Bearer {tmdb_bearer_token}"}
    
    queries_to_try = [title]
    
    # Tier 2: Risky Word Removal
    risky_regex = r"\b(unrated|directors?|cut|extended|edition|version|remastered|part|vol|season|episode|complete|proper|repack)\b"
    title_no_risky = re.sub(risky_regex, "", title, flags=re.IGNORECASE).strip()
    title_no_risky = re.sub(r"\s+", " ", title_no_risky)
    if title_no_risky != title and len(title_no_risky) > 2:
        queries_to_try.append(title_no_risky)

    # Tier 3: Strip Last Words
    words = title.split()
    if len(words) > 1: queries_to_try.append(" ".join(words[:-1]))
    if len(words) > 2: queries_to_try.append(" ".join(words[:-2]))

    queries_to_try = list(dict.fromkeys(queries_to_try))

    async with aiohttp.ClientSession(headers=headers) as session:
        for q in queries_to_try:
            try:
                url = f"{TMDB_BASE_URL}/search/movie?query={quote_plus(q)}&language=en-US"
                async with session.get(url, timeout=10) as resp:
                    if resp.status != 200: continue
                    data = await resp.json()
                
                if not data.get("results"): continue
                
                best = None
                for r in data["results"]:
                    r_year = int(r.get("release_date")[:4]) if r.get("release_date") else None
                    if year and r_year == year: best = r; break
                
                if not best and data["results"]:
                    if fuzz.token_sort_ratio(q, data["results"][0]['title']) > 60:
                        best = data["results"][0]
                
                if best:
                    return {
                        "tmdb_id": best.get("id"), "show_name_from_tmdb": best.get("title"),
                        "poster_path": f"{TMDB_IMAGE_BASE_URL}{best.get('poster_path')}" if best.get("poster_path") else None,
                        "overview": best.get("overview"), "vote_average": best.get("vote_average"),
                        "year": int(best.get("release_date")[:4]) if best.get("release_date") else None,
                        "rating": best.get("vote_average")
                    }
            except Exception: pass
            
    return None

# --- END PART 1 ---
# --- tv_app/tasks.py (PART 2) ---

# ---------------- INGEST & TASKS ----------------
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
    except Exception: return []

def parse_telegram_post(post) -> Optional[Dict]:
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
        if not show_name: show_name = norm_title
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
        return {"show_name_for_search": show_name, "search_year": search_year, "search_season": parse_season_info(season_line), "season_episode_from_post": season_line, "download_link_from_post": download_link, "message_id": int(post.message_id)}
    except Exception: return None

@celery.task(bind=True, retry_backoff=True, max_retries=3)
def update_tv_shows(self):
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
                    time.sleep(0.2)
                    p = parse_telegram_post(post)
                    if not p: continue
                    tmdb = asyncio.run(fetch_tmdb_data(p["show_name_for_search"], p["search_year"], p["search_season"]))
                    if not tmdb: continue
                    existing = TVShow.query.filter_by(tmdb_id=tmdb["tmdb_id"], category=src['type']).first()
                    if existing: db.session.delete(existing); db.session.flush()
                    db.session.add(TVShow(tmdb_id=tmdb["tmdb_id"], message_id=p["message_id"], show_name=tmdb["show_name_from_tmdb"], episode_title=p["season_episode_from_post"], download_link=p["download_link_from_post"], poster_path=tmdb["poster_path"], overview=tmdb["overview"], vote_average=tmdb["vote_average"], year=tmdb["year"], rating=tmdb["rating"], content_hash=f"{tmdb['tmdb_id']}-{p['season_episode_from_post']}", category=src['type']))
                    redis_client.set(f"processed_messages:{post.message_id}", 1, ex=86400)
            db.session.commit()
        except Exception: from tv_app.models import db; db.session.rollback()
        finally: lock.release()

@celery.task(bind=True)
def sync_movies(self):
    redis_client = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    lock = redis_client.lock("sync_movies_lock", timeout=300)
    if not lock.acquire(blocking=False): return
    try:
        uris = []
        if os.environ.get("MONGO_URI_1"): uris.append(os.environ.get("MONGO_URI_1"))
        if os.environ.get("MONGO_URI_2"): uris.append(os.environ.get("MONGO_URI_2"))
        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            for uri in uris:
                try:
                    client = pymongo.MongoClient(uri)
                    col = client[os.environ.get("MONGO_DB_NAME")][os.environ.get("MONGO_COL_NAME")]
                    cursor = col.find({"file_size": {"$gt": 314572800}}).sort("_id", -1).limit(50)
                    for doc in cursor:
                        raw = doc.get('file_name', '')
                        clean, year = clean_movie_filename(raw)
                        
                        if not clean or len(clean) < 2: continue
                        if TVShow.query.filter(TVShow.show_name.ilike(clean), TVShow.category=='movie').first(): continue
                        
                        time.sleep(0.25)
                        tmdb = asyncio.run(fetch_movie_data(clean, year))
                        
                        # V10 Special Retry: If starts with @, try removing 1st word
                        # (fetch_movie_data handles STRIPPING from the END, this handles START garbage)
                        if not tmdb and raw.strip().startswith("@"):
                            parts = clean.split(maxsplit=1)
                            if len(parts) > 1: tmdb = asyncio.run(fetch_movie_data(parts[1], year))
                        
                        if not tmdb: continue
                        db.session.add(TVShow(tmdb_id=tmdb["tmdb_id"], message_id=0, show_name=tmdb["show_name_from_tmdb"], episode_title="Full Movie", download_link=generate_search_link(tmdb["show_name_from_tmdb"]), poster_path=tmdb["poster_path"], overview=tmdb["overview"], vote_average=tmdb["vote_average"], year=tmdb["year"], rating=tmdb["rating"], category='movie', content_hash=f"movie_{tmdb['tmdb_id']}"))
                        db.session.commit()
                except Exception: pass
    except Exception: pass
    finally: lock.release()

@celery.task(bind=True)
def backfill_movies_task(self):
    r = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    last_id = r.get("backfill:checkpoint_id")
    r.hmset("backfill:status", {"state": "running", "current": f"Resuming from {last_id}" if last_id else "Starting...", "progress": 0, "total": 0})
    try:
        tokens_str = os.environ.get("TMDB_BACKFILL_TOKENS", "")
        bk_tokens = [t.strip() for t in tokens_str.split(",") if t.strip()]
        if not bk_tokens: bk_tokens = [os.environ.get("TMDB_BEARER_TOKEN")]
        token_cycle = itertools.cycle(bk_tokens)
        uris = []
        if os.environ.get("MONGO_URI_1"): uris.append(os.environ.get("MONGO_URI_1"))
        if os.environ.get("MONGO_URI_2"): uris.append(os.environ.get("MONGO_URI_2"))
        min_size = 314572800
        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow, SkippedFile
            
            # --- IMPROVED TV FILTER (E11, Episode 05, etc) ---
            def is_obvious_tv(name):
                return re.search(r"\bS\d{1,2}E\d{1,2}\b", name, re.IGNORECASE) or \
                       re.search(r"\b(Episode|Ep)\s*\d+\b", name, re.IGNORECASE) or \
                       re.search(r"\bE\d{2,}\b", name, re.IGNORECASE) or \
                       re.search(r"\bSeason\s*\d+", name, re.IGNORECASE)

            total_docs = 0
            cursors = []
            for uri in uris:
                try:
                    c = pymongo.MongoClient(uri)
                    col = c[os.environ.get("MONGO_DB_NAME")][os.environ.get("MONGO_COL_NAME")]
                    query = {"file_size": {"$gt": min_size}}
                    if last_id: query["_id"] = {"$lt": last_id}
                    total_docs += col.count_documents(query)
                    cursors.append(col.find(query).sort("_id", -1))
                except: pass
            
            r.hset("backfill:status", "total", total_docs)
            processed = 0
            
            for cursor in cursors:
                for doc in cursor:
                    state = r.hget("backfill:status", "state")
                    if state == "stopped": return
                    while state == "paused": time.sleep(2); state = r.hget("backfill:status", "state"); 
                    if state == "stopped": return

                    processed += 1
                    if processed % 5 == 0: r.hset("backfill:status", "progress", processed)
                    
                    cur_id = doc.get('_id')
                    raw = doc.get('file_name', '')
                    r.hset("backfill:status", "current", raw[:20])
                    
                    # 1. Skip TV Shows
                    if is_obvious_tv(raw):
                        r.set("backfill:checkpoint_id", cur_id); continue

                    clean, year = clean_movie_filename(raw)

                    # 2. Skip existing
                    if clean and len(clean) > 1:
                        if TVShow.query.filter(TVShow.show_name.ilike(clean), TVShow.category=='movie').first():
                            r.set("backfill:checkpoint_id", cur_id); continue

                    # 3. Log Clean Failures
                    if not clean or len(clean) < 2:
                        try:
                            if not SkippedFile.query.filter_by(filename=raw).first():
                                db.session.add(SkippedFile(filename=raw, reason="Cleaner Failed"))
                                db.session.commit()
                        except: db.session.rollback()
                        r.set("backfill:checkpoint_id", cur_id); continue
                    
                    cur_token = next(token_cycle)
                    time.sleep(0.2)
                    
                    try:
                        # fetch_movie_data handles TIER 1, 2, 3 (Clean, Risky-Removed, Stripped-Last)
                        meta = asyncio.run(fetch_movie_data(clean, year))
                        
                        # Special V10 Retry: If all failed, and file started with @, try removing 1st word
                        # This handles: "@Uploader JunkTitle Movie"
                        if not meta and raw.strip().startswith("@"):
                            parts = clean.split(maxsplit=1)
                            if len(parts) > 1:
                                meta = asyncio.run(fetch_movie_data(parts[1], year))
                                if meta: clean = parts[1]

                        if meta:
                            db.session.add(TVShow(tmdb_id=meta["id"], message_id=0, show_name=meta["title"], episode_title="Full Movie", download_link=generate_search_link(meta["title"]), poster_path=f"{TMDB_IMAGE_BASE_URL}{meta.get('poster_path')}" if meta.get('poster_path') else None, overview=meta.get("overview"), vote_average=meta.get("vote_average"), year=int(meta["release_date"][:4]) if meta.get("release_date") else None, rating=meta.get("vote_average"), category='movie', content_hash=f"movie_{meta['id']}"))
                            db.session.commit()
                            r.incr("backfill:added") # Increment Added Counter
                        else:
                            try:
                                if not SkippedFile.query.filter_by(filename=raw).first():
                                    db.session.add(SkippedFile(filename=raw, reason=f"TMDb No: {clean}"))
                                    db.session.commit()
                                    r.incr("backfill:skipped") # Increment Skipped Counter
                            except: db.session.rollback()
                        r.set("backfill:checkpoint_id", cur_id)
                    except Exception: db.session.rollback()

    except Exception as e: r.hset("backfill:status", "state", f"Error: {e}")
    r.hset("backfill:status", "state", "complete")

@celery.task(name="tv_app.tasks.reset_clicks")
def reset_clicks():
    from tv_app.app import app
    with app.app_context():
        from tv_app.models import db, TVShow
        TVShow.query.update({TVShow.clicks: 0}); db.session.commit()

@celery.task(name="tv_app.tasks.test_task")
def test_task(): return "OK"
