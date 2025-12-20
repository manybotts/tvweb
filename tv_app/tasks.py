# --- tv_app/tasks.py (PART 1: Infrastructure & TV/Anime Logic) ---
import os
import re
import asyncio
import logging
import itertools
import hashlib
from typing import Dict, Optional, List, Any
from urllib.parse import quote_plus
from datetime import datetime

import aiohttp
from celery import Celery
from dotenv import load_dotenv
from redis import Redis
from thefuzz import fuzz, process
from pymongo import MongoClient, DESCENDING

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# STRICT ADHERENCE: Loading config from celeryconfig.py
celery = Celery(__name__)
celery.config_from_object("celeryconfig")

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# ---------------- Text Helpers ----------------
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
        if ratio > 2.0: 
            base -= 15
        elif ratio > 1.5:
            base -= 5

    return base

def parse_season_info(line: str) -> Optional[int]:
    nums = re.findall(r"\d+", line)
    return max(int(n) for n in nums) if nums else None

# ---------------- TV/Anime: Telegram Ingest ----------------

async def fetch_new_telegram_posts(channel_env_var: str, redis_key_suffix: str) -> list:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get(channel_env_var)
    
    if not channel_id:
        if channel_env_var == 'TELEGRAM_CHANNEL_ID':
            logger.error(f"Missing env var: {channel_env_var}")
        return []

    redis_client = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    last_offset_key = f"last_telegram_update_id:{redis_key_suffix}"
    last_offset = int(redis_client.get(last_offset_key) or 0)

    from telegram.ext import Application
    try:
        app = Application.builder().token(token).build()
        updates = await app.bot.get_updates(
            offset=last_offset + 1,
            allowed_updates=["channel_post", "edited_channel_post"],
            timeout=60,
        )
        if hasattr(app, 'shutdown'):
            await app.shutdown()

        posts = []
        for u in updates:
            p = u.channel_post or u.edited_channel_post
            if p and p.sender_chat and str(p.sender_chat.id) == channel_id and p.caption:
                posts.append(p)

        if updates:
            redis_client.set(last_offset_key, updates[-1].update_id)
        return posts
    except Exception as e:
        logger.exception(f"Error fetching Telegram posts for {channel_env_var}: {e}")
        return []

def parse_telegram_post(post) -> Optional[Dict]:
    """Parses standard TV/Anime posts."""
    try:
        text = post.caption
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2: return None

        title_line = lines[0]
        season_line = lines[1]

        clean_line = re.sub(r"[\[\]\(\)]", " ", title_line)
        norm_title = normalize(clean_line)
        year_match = re.search(r"(\d{4})$", norm_title)
        
        search_year = None
        show_name_for_search = norm_title

        if year_match:
            search_year = int(year_match.group(1))
            show_name_for_search = re.sub(r"\s*\d{4}$", "", norm_title).strip()

        if not show_name_for_search:
            show_name_for_search = norm_title

        search_season = parse_season_info(season_line)

        # Link extraction
        download_link_from_post = None
        if post.caption_entities:
            for ent in post.caption_entities:
                if ent.type == "text_link":
                    et = text[ent.offset: ent.offset + ent.length]
                    if "click here" in et.lower():
                        download_link_from_post = ent.url
                        break
        if not download_link_from_post and post.caption_entities:
            for ent in reversed(post.caption_entities):
                if ent.type == "text_link":
                    et = text[ent.offset: ent.offset + ent.length]
                    if "#_" not in et:
                        download_link_from_post = ent.url
                        break
        if not download_link_from_post:
            for ln in reversed(lines):
                if "#_" in ln: continue
                m = re.search(r"(https?://\S+)", ln)
                if m:
                    download_link_from_post = m.group(1)
                    break

        return {
            "show_name_for_search": show_name_for_search,
            "search_year": search_year,
            "search_season": search_season,
            "season_episode_from_post": season_line,
            "download_link_from_post": download_link_from_post,
            "message_id": int(post.message_id),
        }
    except Exception as e:
        logger.exception(f"Error parsing post {post.message_id}: {e}")
        return None

# --------------- tmdb lookup (TV/Anime) ----------------

async def fetch_tmdb_tv_data(show_name: str, search_year: Optional[int], search_season: Optional[int]) -> Optional[Dict]:
    """
    Exclusively for TV Shows/Anime (uses /search/tv).
    """
    tmdb_bearer_token = os.environ.get("TMDB_BEARER_TOKEN")
    headers = {"Authorization": f"Bearer {tmdb_bearer_token}"}

    q_name = show_name.strip()
    async with aiohttp.ClientSession(headers=headers) as session:
        search_url = f"{TMDB_BASE_URL}/search/tv?query={quote_plus(q_name)}&language=en-US"
        try:
            async with session.get(search_url, timeout=10) as resp:
                resp.raise_for_status()
                search_data = await resp.json()
        except Exception as e:
            logger.error(f"TMDb TV search failed for '{q_name}': {e}")
            return None

        if not search_data.get("results"):
            return None

        detailed = []
        for r in search_data["results"]:
            detail_url = f"{TMDB_BASE_URL}/tv/{r['id']}?language=en-US"
            try:
                async with session.get(detail_url, timeout=5) as d:
                    if d.status == 200:
                        detailed.append(await d.json())
            except Exception:
                continue
        if not detailed:
            return None

        best = (None, -1)
        qn = normalize(q_name)

        for r in detailed:
            name = r.get("name") or ""
            oname = r.get("original_name") or ""
            
            s = max(strong_title_score(q_name, name), strong_title_score(q_name, oname))

            fa = r.get("first_air_date") or ""
            if search_year and fa[:4].isdigit() and int(fa[:4]) == search_year:
                s += 10 

            if search_season:
                sc = int(r.get("number_of_seasons") or 0)
                if sc >= search_season:
                    s += max(0, 6 - abs(sc - search_season))

            if s > best[1]:
                best = (r, s)

        found = best[0]
        
        if not found or best[1] < 50:
            names = [r.get("name") for r in detailed if r.get("name")]
            pick = process.extractOne(qn, names, scorer=fuzz.token_set_ratio)
            if pick:
                for r in detailed:
                    if r.get("name") == pick[0]:
                        found = r
                        break

        if not found:
            return None

        year = None
        fa = found.get("first_air_date") or ""
        if fa[:4].isdigit():
            year = int(fa[:4])

        return {
            "tmdb_id": found.get("id"),
            "show_name_from_tmdb": found.get("name"),
            "poster_path": f"{TMDB_IMAGE_BASE_URL}{found.get('poster_path')}" if found.get("poster_path") else None,
            "overview": found.get("overview"),
            "vote_average": found.get("vote_average"),
            "year": year,
            "rating": found.get("vote_average"),
        }

# --------------- tasks (TV/Anime + Utility) ----------------

@celery.task(bind=True, retry_backoff=True, max_retries=3)
def update_tv_shows(self):
    redis_client = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=120)
    if not lock.acquire(blocking=False): return
    
    from tv_app.app import app
    with app.app_context():
        try:
            from tv_app.models import db, TVShow

            sources = [
                {'type': 'tv', 'env_var': 'TELEGRAM_CHANNEL_ID', 'offset_key': 'tv_main'},
                {'type': 'anime', 'env_var': 'TELEGRAM_ANIME_CHANNEL_ID', 'offset_key': 'anime_main'}
            ]

            for source in sources:
                posts = asyncio.run(fetch_new_telegram_posts(source['env_var'], source['offset_key']))
                if not posts: continue

                for post in posts:
                    processed_key = f"processed_messages:{post.message_id}"
                    if redis_client.exists(processed_key): continue

                    parsed = parse_telegram_post(post)
                    if not parsed: continue

                    # Uses the specific TV fetcher now
                    tmdb = asyncio.run(
                        fetch_tmdb_tv_data(
                            parsed["show_name_for_search"],
                            parsed["search_year"],
                            parsed["search_season"],
                        )
                    )
                    if not tmdb: continue

                    tmdb_id = tmdb["tmdb_id"]
                    current_category = source['type']

                    existing = TVShow.query.filter_by(
                        tmdb_id=tmdb_id, 
                        category=current_category
                    ).first()

                    if existing:
                        db.session.delete(existing)
                        db.session.flush()

                    new_show = TVShow(
                        tmdb_id=tmdb_id,
                        message_id=parsed["message_id"],
                        show_name=tmdb["show_name_from_tmdb"],
                        episode_title=parsed["season_episode_from_post"],
                        download_link=parsed["download_link_from_post"],
                        poster_path=tmdb["poster_path"],
                        overview=tmdb["overview"],
                        vote_average=tmdb["vote_average"],
                        year=tmdb["year"],
                        rating=tmdb["rating"],
                        content_hash=f"{tmdb_id}-{parsed['season_episode_from_post']}",
                        category=current_category 
                    )
                    db.session.add(new_show)
                    redis_client.set(processed_key, 1, ex=86400)

            db.session.commit()
            
        except Exception as e:
            logger.exception(f"Error in update_tv_shows: {e}")
            from tv_app.models import db
            db.session.rollback()
        finally:
            if lock.locked(): lock.release()

@celery.task(name="tv_app.tasks.reset_clicks")
def reset_clicks():
    try:
        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            TVShow.query.update({TVShow.clicks: 0})
            db.session.commit()
    except Exception as e:
        logger.exception(f"Error in reset_clicks: {e}")
        from tv_app.models import db
        db.session.rollback()

@celery.task(name="tv_app.tasks.test_task")
def test_task():
    return "Test task complete"

# ... (Continue to Part 2 for Movie Logic) ...
# ==============================================================================
#                               MOVIE LOGIC (UPDATED)
# ==============================================================================

# Initialize Token Cycle for Backfill
_tokens_env = os.environ.get("TMDB_BACKFILL_TOKENS", "")
_token_list = [t.strip() for t in _tokens_env.split(",") if t.strip()]
if not _token_list:
    _token_list = [os.environ.get("TMDB_BEARER_TOKEN")]
_tmdb_token_cycle = itertools.cycle(_token_list)

def get_next_tmdb_token():
    """Rotates through tokens to avoid rate limits during backfill."""
    return next(_tmdb_token_cycle)

def sanitize_movie_filename(filename: str) -> Dict[str, Any]:
    """
    Sanitizes AutoFilter filenames:
    1. Removes spam prefixes (@Channel, www).
    2. Strips technical jargon (1080p, x264, etc).
    3. Extracts the year.
    """
    # 1. Prefix Removal
    clean = re.sub(r"^(@\w+|www\.\S+)\s+", "", filename).strip()
    
    # 2. Year Extraction
    year = None
    year_match = re.search(r"\b(19|20)\d{2}\b", clean)
    if year_match:
        year = int(year_match.group(0))
    
    # 3. Technical Stripping (Aggressive)
    tech_pattern = re.compile(r"(?i)\b(1080p|720p|480p|x264|x265|hevc|aac|bluray|web-dl|hdr|dvdrip|camrip|brrip)\b.*")
    clean = tech_pattern.sub("", clean)
    
    # 4. Cleanup
    clean = re.sub(r"[._-]", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    
    return {"raw_title": clean, "year": year}

def generate_movie_deep_link(title: str) -> str:
    """Generates deep link: https://t.me/{BOT}?start=search_{CLEAN_TITLE}"""
    # FIXED: Uses BOT_USERNAME from your .env
    bot_username = os.environ.get("BOT_USERNAME", "iBoxTVBot")
    
    # Replace non-alphanumeric with underscore and truncate
    clean = re.sub(r"[^a-zA-Z0-9]", "_", title)[:50]
    return f"https://t.me/{bot_username}?start=search_{clean}"

def mongo_id_to_int(oid: str) -> int:
    """
    CRITICAL FIX: Synthesizes a deterministic 64-bit integer from a String ID.
    The AutoFilter bot uses string file_ids (e.g. "AgAD..."), not Mongo ObjectIds.
    """
    if not oid: return 0
    # Hash the string to a large int, then modulo to fit in Signed 64-bit BigInt
    return int(hashlib.sha256(str(oid).encode('utf-8')).hexdigest(), 16) % (2**63 - 1)

async def process_single_movie(file_data: Dict, tmdb_token: str) -> Optional[Dict]:
    """
    Orchestrates the check for a single movie file:
    Sanitize -> Search TMDb -> Match -> Return Data
    """
    # FIX: AutoFilter uses 'file_name', not 'filename'
    fname = file_data.get('file_name', '')
    sanitized = sanitize_movie_filename(fname)
    query = sanitized['raw_title']
    year = sanitized['year']

    if not query or len(query) < 2:
        return None

    headers = {"Authorization": f"Bearer {tmdb_token}"}
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. Search
        search_url = f"{TMDB_BASE_URL}/search/movie?query={quote_plus(query)}&language=en-US"
        if year:
            search_url += f"&primary_release_year={year}"
            
        try:
            async with session.get(search_url, timeout=5) as resp:
                if resp.status == 429:
                    # Rate limit hit, return special flag
                    return "RATE_LIMIT"
                if resp.status != 200:
                    return None
                data = await resp.json()
        except Exception:
            return None

        if not data.get('results'):
            return None

        # 2. Match Validation
        best_match = None
        best_score = 0
        
        for res in data['results']:
            title = res.get('title', '')
            # Similarity Gate: > 80
            score = fuzz.token_sort_ratio(query.lower(), title.lower())
            
            # Boost if year matches exactly
            res_date = res.get('release_date', '')
            if year and res_date.startswith(str(year)):
                score += 10
            
            if score > best_score:
                best_score = score
                best_match = res

        if not best_match or best_score < 80:
            return None

        # 3. Format Data
        return {
            'tmdb_id': best_match['id'],
            'show_name': best_match['title'],
            'overview': best_match.get('overview'),
            'poster_path': f"{TMDB_IMAGE_BASE_URL}{best_match.get('poster_path')}" if best_match.get('poster_path') else None,
            'vote_average': best_match.get('vote_average'),
            'release_date': best_match.get('release_date'),
            'year': int(best_match['release_date'][:4]) if best_match.get('release_date') else None
        }

@celery.task(bind=True, name="tv_app.tasks.backfill_movies_task")
def backfill_movies_task(self):
    """
    Phase 5: The Backfill Engine.
    Iterates through configured collection using String ID checkpoints.
    """
    redis_client = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    
    if redis_client.get("backfill:pause"):
        logger.info("Backfill paused by user.")
        return "Paused"

    from tv_app.app import app
    with app.app_context():
        from tv_app.models import db, TVShow, SkippedFile

        uris = [os.environ.get("MONGO_URI_1"), os.environ.get("MONGO_URI_2")]
        uris = [u for u in uris if u]
        
        # CRITICAL FIX: Explicitly use DB and Collection from .env
        target_db_name = os.environ.get('MONGO_DB_NAME', 'Huswy')
        target_col_name = os.environ.get('MONGO_COL_NAME', 'Husw')

        total_processed = 0
        
        for uri in uris:
            try:
                client = MongoClient(uri)
                # FIX: Access DB explicitly by name, do not use get_default_database()
                db_obj = client[target_db_name]
                coll = db_obj[target_col_name]

                # Retrieve Checkpoint (Last processed String ID)
                checkpoint_key = f"backfill:checkpoint:{target_db_name}"
                last_id = redis_client.get(checkpoint_key)
                
                # Filter for files > 300MB
                query = {"file_size": {"$gt": 314572800}} 
                
                # If we have a checkpoint, get items 'less than' it (walking backwards)
                if last_id:
                    query["_id"] = {"$lt": last_id}

                # Sort by _id DESC to walk backwards
                cursor = coll.find(query).sort("_id", DESCENDING).limit(100)

                batch = list(cursor)
                if not batch:
                    continue

                for doc in batch:
                    if redis_client.get("backfill:pause"):
                        break

                    # FIX: Use 'file_name' (AutoFilter standard)
                    file_name = doc.get('file_name')
                    
                    # 1. Check Skipped
                    if SkippedFile.query.filter_by(filename=file_name).first():
                        continue
                    
                    # 2. Process
                    token = get_next_tmdb_token()
                    result = asyncio.run(process_single_movie({'file_name': file_name}, token))

                    if result == "RATE_LIMIT":
                        break 
                    
                    if not result:
                        db.session.add(SkippedFile(filename=file_name, reason="No Match or Low Score"))
                        db.session.commit()
                        continue

                    # 3. Save
                    # Convert String ID to Int Hash
                    syn_id = mongo_id_to_int(doc['_id'])
                    
                    if not TVShow.query.filter_by(tmdb_id=result['tmdb_id'], category='movie').first():
                        show = TVShow(
                            tmdb_id=result['tmdb_id'],
                            message_id=syn_id,
                            show_name=result['show_name'],
                            overview=result['overview'],
                            poster_path=result['poster_path'],
                            vote_average=result['vote_average'],
                            year=result['year'],
                            rating=result['vote_average'],
                            category='movie',
                            download_link=generate_movie_deep_link(result['show_name']),
                            content_hash=str(doc['_id']),
                            slug=None
                        )
                        db.session.add(show)
                        db.session.commit()
                        
                        redis_client.hincrby("backfill:status", "added", 1)

                    # Update Checkpoint with the String ID
                    redis_client.set(checkpoint_key, str(doc['_id']))
                    total_processed += 1

                client.close()
                
            except Exception as e:
                logger.error(f"Backfill Error on {uri}: {e}")
                continue

    return f"Processed {total_processed} movies."

@celery.task(name="tv_app.tasks.sync_movies")
def sync_movies():
    """
    Phase 6: The Sync Engine.
    Polls for new movies (approx top 50 recently added).
    """
    redis_client = Redis.from_url(os.environ.get("REDIS_URL"), decode_responses=True)
    
    from tv_app.app import app
    with app.app_context():
        from tv_app.models import db, TVShow, SkippedFile

        uris = [os.environ.get("MONGO_URI_1"), os.environ.get("MONGO_URI_2")]
        uris = [u for u in uris if u]

        # CRITICAL FIX: Explicit DB/Col Config
        target_db_name = os.environ.get('MONGO_DB_NAME', 'Huswy')
        target_col_name = os.environ.get('MONGO_COL_NAME', 'Husw')

        for uri in uris:
            try:
                client = MongoClient(uri)
                db_obj = client[target_db_name]
                coll = db_obj[target_col_name]

                # Sort by natural insertion order (reverse) to get latest
                cursor = coll.find({"file_size": {"$gt": 314572800}}).sort("$natural", DESCENDING).limit(50)

                for doc in cursor:
                    file_name = doc.get('file_name')
                    
                    # Convert ID
                    syn_id = mongo_id_to_int(doc['_id'])
                    
                    # Fast check
                    if TVShow.query.filter_by(message_id=syn_id, category='movie').first():
                        continue
                    if SkippedFile.query.filter_by(filename=file_name).first():
                        continue

                    # Process
                    token = os.environ.get("TMDB_BEARER_TOKEN")
                    result = asyncio.run(process_single_movie({'file_name': file_name}, token))

                    if not result:
                        db.session.add(SkippedFile(filename=file_name, reason="Sync: No Match"))
                        db.session.commit()
                        continue

                    if not TVShow.query.filter_by(tmdb_id=result['tmdb_id'], category='movie').first():
                        show = TVShow(
                            tmdb_id=result['tmdb_id'],
                            message_id=syn_id,
                            show_name=result['show_name'],
                            overview=result['overview'],
                            poster_path=result['poster_path'],
                            vote_average=result['vote_average'],
                            year=result['year'],
                            rating=result['vote_average'],
                            category='movie',
                            download_link=generate_movie_deep_link(result['show_name']),
                            content_hash=str(doc['_id']),
                            slug=None
                        )
                        db.session.add(show)
                        db.session.commit()
                        logger.info(f"Sync: Added movie {result['show_name']}")

                client.close()
            except Exception as e:
                logger.error(f"Sync Error: {e}")
