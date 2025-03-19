# tasks.py
from celery import Celery
from celery.exceptions import MaxRetriesExceededError
import os, requests, logging, asyncio, hashlib, re, unicodedata, json, random
from telegram import Bot
from telegram.error import TelegramError
from telegram.ext import Application
from urllib.parse import quote_plus
from dotenv import load_dotenv
from redis import Redis
from ratelimit import limits, sleep_and_retry
from thefuzz import fuzz, process

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
celery = Celery(__name__)
celery.config_from_object('celeryconfig')
TMDB_CALLS_PER_SECOND = 4
TMDB_PERIOD = 1

def calculate_content_hash(show_name, episode_title, download_link):
    content_string = f"{show_name or ''}-{episode_title or ''}-{download_link or ''}"
    return hashlib.sha256(content_string.encode('utf-8')).hexdigest()

def normalize_string(text):
    if text is None: return ""
    text = text.lower()
    text = ''.join(c for c in text if unicodedata.category(c)[0] != 'C')
    text = re.sub(r'[^\w\s,&\'-]', '', text)
    return re.sub(r'\s+', ' ', text).strip()

async def fetch_new_telegram_posts():
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    last_offset_key = f"last_telegram_update_id:{channel_id}"
    last_offset = redis_client.get(last_offset_key) or 0
    logger.info(f"Last Telegram Update ID for channel {channel_id}: {last_offset}")
    try:
        appli = Application.builder().token(token).build()
        updates = await appli.bot.get_updates(offset=int(last_offset) + 1, allowed_updates=['channel_post'], timeout=60)
        await appli.shutdown()
        new_posts = [update.channel_post for update in updates
                     if update.channel_post and update.channel_post.sender_chat and str(update.channel_post.sender_chat.id) == channel_id and update.channel_post.caption]
        if updates: redis_client.set(last_offset_key, updates[-1].update_id)
        return new_posts
    except (TelegramError, Exception) as e:
        logger.exception(f"Telegram error or unexpected error: {e}")
        return []

def parse_telegram_post(post):
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.message_id}, Caption: {text!r}")
        # *** CORRECTED LINE BELOW - TRIPLE CHECKED! ***
        filtered_lines = [line.strip() for line in text.splitlines() if not line.strip().startswith(("#", "#_"))]
        show_name = filtered_lines[0] if filtered_lines else None
        season_episode = filtered_lines[1] if len(filtered_lines) >= 2 else None
        download_link = next((entity.url for entity in post.caption_entities if entity.type == 'text_link'), None) if post.caption_entities else None
        normalized_text = normalize_string("\n".join(filtered_lines))
        if not season_episode:
            match = re.search(r'(?:s|season)\s*(\d+)\s*(?:e|episode)\s*(\d+)|(\d+)[xX](\d+)', normalized_text, re.IGNORECASE)
            if match: season_episode = f"S{match.group(1).zfill(2)}E{match.group(2).zfill(2)}" if match.group(1) else f"{match.group(3)}x{match.group(4).zfill(2)}"
        if not download_link:
            url_match = re.search(r'(https?://\S+)', normalized_text, re.MULTILINE)
            download_link = url_match.group(1) if url_match else None
        if show_name:
            return {'show_name': normalize_string(show_name), 'season_episode': season_episode, 'download_link': download_link, 'message_id': post.message_id}
        else:
            logger.warning(f"No show name found in post: {post.message_id}")
            return None
    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None

@sleep_and_retry
@limits(calls=TMDB_CALLS_PER_SECOND, period=TMDB_PERIOD)
def fetch_tmdb_data(show_name, language='en-US'):
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    cache_key = f"tmdb:{show_name.lower().replace(' ', '_')}"
    cached_data = redis_client.get(cache_key)
    if cached_data: return json.loads(cached_data)
    api_keys_string = os.environ.get('TMDB_API_KEYS')
    if not api_keys_string: raise ValueError("No TMDb API keys found!")
    api_key = random.choice(api_keys_string.split(',')).strip()
    try:
        logger.info(f"Fetching TMDb data for: {show_name} using API key: {api_key[:4]}...")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}&include_adult=false"
        search_response = requests.get(search_url, headers=headers, timeout=10)
        search_response.raise_for_status()
        search_data = search_response.json()
        if search_data['results']: show_id = search_data['results'][0]['id']
        else:
            logger.warning(f"No direct match for: {show_name}. Attempting fuzzy match.")
            search_url = f"https://api.themoviedb.org/3/search/tv?query={quote_plus(show_name)}&language={language}&page=1&include_adult=false"
            search_response = requests.get(search_url, headers=headers, timeout=10)
            search_response.raise_for_status()
            all_results = search_response.json()['results']
            show_titles = [result['name'] for result in all_results]
            best_match, score = process.extractOne(show_name, show_titles)
            if score >= 80:
                for result in all_results:
                    if result['name'] == best_match:
                        show_id = result['id']
                        logger.info(f"Fuzzy match found: {best_match} (score: {score}) for {show_name}")
                        break
            else:
                logger.warning(f"No close match found for: {show_name} (best score: {score})")
                return None
        details_url = f"https://api.themoviedb.org/3/tv/{show_id}?language={language}"
        details_response = requests.get(details_url, headers=headers, timeout=10)
        details_response.raise_for_status()
        details_data = details_response.json()
        genres = [genre['name'] for genre in details_data.get('genres', [])]
        genre_string = ", ".join(genres)
        first_air_date = details_data.get('first_air_date')
        year = int(first_air_date[:4]) if first_air_date else None
        number_of_seasons = details_data.get('number_of_seasons')
        latest_season_episode = f"S{details_data['last_episode_to_air']['season_number']:02d}E{details_data['last_episode_to_air']['episode_number']:02d}" if details_data.get('last_episode_to_air') else None
        tmdb_info = {'poster_path': f"https://image.tmdb.org/t/p/w500{details_data.get('poster_path')}" if details_data.get('poster_path') else None, 'overview': details_data.get('overview'), 'vote_average': details_data.get('vote_average'), 'latest_season_episode': latest_season_episode, 'genre': genre_string, 'year': year, 'number_of_seasons': number_of_seasons}
        redis_client.setex(cache_key, 86400, json.dumps(tmdb_info))
        logger.info(f"Cached TMDb data for: {show_name}")
        return tmdb_info
    except (requests.exceptions.RequestException, Exception) as e:
        logger.exception(f"Error fetching data from TMDb or unexpected error: {e}")
        return None

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    redis_client = Redis.from_url(os.environ.get('REDIS_URL'), decode_responses=True)
    lock = redis_client.lock("update_tv_shows_lock", timeout=60, blocking_timeout=5)
    if not lock.acquire(blocking=False):
        logger.info("Could not acquire lock, task is likely already running.")
        return
    try:
        logger.info("Lock acquired, starting update_tv_shows task.")
        posts = asyncio.run(fetch_new_telegram_posts())
        if not posts:
            logger.info("No new posts found.")
            return
        from tv_app.app import app
        with app.app_context():
            from tv_app.models import db, TVShow
            for post in posts:
                if redis_client.sismember("processed_messages", post.message_id): continue
                parsed_data = parse_telegram_post(post)
                if not parsed_data: continue
                logger.info(f"Processing show: {parsed_data['show_name']}")
                tmdb_data = fetch_tmdb_data(parsed_data['show_name'])
                if not tmdb_data: continue
                new_content_hash = calculate_content_hash(parsed_data['show_name'], parsed_data['season_episode'], parsed_data['download_link'])
                existing_show = TVShow.query.filter_by(show_name=parsed_data['show_name']).first()
                episode_title = parsed_data['season_episode'] or tmdb_data.get('latest_season_episode')
                season_range = f"1-{tmdb_data['number_of_seasons']}" if tmdb_data.get('number_of_seasons', 0) > 1 else str(tmdb_data.get('number_of_seasons')) if tmdb_data.get('number_of_seasons') else None

                if existing_show:
                    logger.info(f"Updating existing show: {parsed_data['show_name']}")
                    existing_show.episode_title = episode_title
                    existing_show.download_link, existing_show.message_id = parsed_data['download_link'], post.message_id
                    existing_show.overview, existing_show.vote_average = tmdb_data.get('overview'), tmdb_data.get('vote_average')
                    existing_show.poster_path, existing_show.content_hash = tmdb_data.get('poster_path'), new_content_hash
                    existing_show.genre, existing_show.year = tmdb_data.get('genre'), tmdb_data.get('year')
                    if season_range:
                        try:
                            current_max_season = int(existing_show.season_range.split('-')[-1]) if existing_show.season_range else 0
                            new_max_season = int(season_range.split('-')[-1])
                            if new_max_season > current_max_season: existing_show.season_range = season_range
                        except (ValueError, AttributeError): existing_show.season_range = season_range
                    db.session.commit()
                    logger.info(f"Successfully updated: {parsed_data['show_name']}")
                else:
                    logger.info(f"Inserting new show: {parsed_data['show_name']}")
                    show_data = {'show_name': parsed_data['show_name'], 'episode_title': episode_title, 'download_link': parsed_data['download_link'], 'message_id': post.message_id, 'overview': tmdb_data.get('overview'), 'vote_average': tmdb_data.get('vote_average'), 'poster_path': tmdb_data.get('poster_path'), 'content_hash': new_content_hash, 'genre': tmdb_data.get('genre'), 'year': tmdb_data.get('year'), 'season_range': season_range}
                    new_show = TVShow(**show_data)
                    db.session.add(new_show)
                    db.session.commit()
                    logger.info(f"Successfully inserted: {parsed_data['show_name']}")
                redis_client.sadd("processed_messages", post.message_id)
            db.session.remove()
    except MaxRetriesExceededError:
        logger.error("Max retries exceeded for update_tv_shows task.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        logger.error(f"Task ID: {self.request.id}")
        self.retry(exc=e, countdown=60)
    finally:
        lock.release()
        logger.info("Lock released.")

@celery.task
def test_task():
    logger.info("The test Celery task has run!")
    return "Test task complete"
