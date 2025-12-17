# tv_app/tasks.py — Smarter scoring for short titles (FBI fix)
import os
import re
import asyncio
import logging
from typing import Dict, Optional, List

import aiohttp
from celery import Celery
from dotenv import load_dotenv
from redis import Redis
from thefuzz import fuzz, process
from urllib.parse import quote_plus

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

celery = Celery(__name__)
celery.config_from_object("celeryconfig")

TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"

# ---------------- text helpers ----------------
_ACRONYM_DOTS = re.compile(r"\b([A-Z]\.){2,}\b")       # A.T.O.M.
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

def stemlike(a: str, b: str) -> bool:
    if not a or not b: return False
    x, y = a.lower(), b.lower()
    shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
    return longer.startswith(shorter) and len(longer) - len(shorter) <= 2

def strong_title_score(query: str, candidate: str) -> int:
    """
    Calculates similarity, but penalizes results that are significantly longer 
    than the query (to prevent 'FBI' matching 'Enemies: ... FBI').
    """
    qn = normalize(query)
    cn = normalize(candidate)

    # 1. Exact Match is King
    if qn == cn:
        return 100
    
    # 2. Article-stripped Exact Match
    sq = strip_leading_article(qn)
    sc = strip_leading_article(cn)
    if sq == sc:
        return 99

    # 3. Fuzzy Matching
    # token_sort_ratio is better here than token_set_ratio for short titles
    # because it penalizes extra words.
    base = fuzz.token_sort_ratio(qn, cn)

    # 4. Length Penalty (The FBI Fix)
    # If the candidate is much longer than the query, punish it.
    len_q = len(qn)
    len_c = len(cn)
    
    if len_q > 0 and len_c > len_q:
        ratio = len_c / len_q
        # If candidate is more than 2x longer, subtract points
        if ratio > 2.0: 
            base -= 15
        elif ratio > 1.5:
            base -= 5

    return base

def parse_season_info(line: str) -> Optional[int]:
    nums = re.findall(r"\d+", line)
    return max(int(n) for n in nums) if nums else None

# --------------- telegram ingest ----------------
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
    try:
        text = post.caption
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2: return None

        title_line = lines[0]
        season_line = lines[1]

        # --- FIX: AGGRESSIVE YEAR STRIPPING ---
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

# --------------- tmdb lookup ----------------
async def fetch_tmdb_data(show_name: str, search_year: Optional[int], search_season: Optional[int]) -> Optional[Dict]:
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
            logger.error(f"TMDb search failed for '{q_name}': {e}")
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
        
        # Use simple normalization for the loop
        qn = normalize(q_name)

        for r in detailed:
            name = r.get("name") or ""
            oname = r.get("original_name") or ""
            
            # Use new smarter scoring
            s = max(strong_title_score(q_name, name), strong_title_score(q_name, oname))

            fa = r.get("first_air_date") or ""
            # YEAR MATCH BOOST
            if search_year and fa[:4].isdigit() and int(fa[:4]) == search_year:
                s += 10 # Increase year confidence

            if search_season:
                sc = int(r.get("number_of_seasons") or 0)
                if sc >= search_season:
                    s += max(0, 6 - abs(sc - search_season))

            if s > best[1]:
                best = (r, s)

        found = best[0]
        
        # Fallback if score is too low?
        if not found or best[1] < 50:
            # If our smart score failed, try fuzz as last resort
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

        logger.info(f"TMDb resolved '{q_name}' -> {found.get('name')} (id={found.get('id')})")

        return {
            "tmdb_id": found.get("id"),
            "show_name_from_tmdb": found.get("name"),
            "poster_path": f"{TMDB_IMAGE_BASE_URL}{found.get('poster_path')}" if found.get("poster_path") else None,
            "overview": found.get("overview"),
            "vote_average": found.get("vote_average"),
            "year": year,
            "rating": found.get("vote_average"),
        }

# --------------- tasks ----------------
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

                    tmdb = asyncio.run(
                        fetch_tmdb_data(
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
                                             
