from celery import Celery
from celery.schedules import crontab
import os
import logging
import redis
from dotenv import load_dotenv
import telegram
from telegram.error import NetworkError, RetryAfter, TimedOut
import asyncio

# Load environment variables
load_dotenv()

# --- Configuration ---
# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Telegram Bot Token (Ensure this is set in your environment)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
# Telegram Channel ID (Ensure this is set in your environment)
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
REDIS_URL = os.environ.get('REDIS_URL')

# --- Celery Setup ---
celery = Celery(__name__)
celery.conf.broker_url = REDIS_URL
celery.conf.result_backend = REDIS_URL
celery.conf.timezone = 'UTC'

# --- Redis Client ---
redis_client = redis.StrictRedis.from_url(REDIS_URL, decode_responses=True)

# --- Celery Beat Schedule ---
@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    # Log the current time every 15 seconds (for testing)
    sender.add_periodic_task(15.0, log_current_time.s(), name='log-time-every-15-seconds')
    # Fetch Telegram posts and update the database every minute
    sender.add_periodic_task(
        crontab(minute='*'),  # Every minute
        update_tv_shows.s(),
        name='update-tv-shows-every-minute'
    )


# --- Helper Functions ---
async def fetch_new_telegram_posts(bot):
    """Fetches new posts from the specified Telegram channel."""
    channel_id = int(TELEGRAM_CHANNEL_ID)
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID environment variable not set!")
        return []

    last_update_id = redis_client.get('last_telegram_update_id')
    logger.info(f"Last update ID from Redis: {last_update_id}")  # Log the retrieved ID
    last_update_id = int(last_update_id) if last_update_id else None
    new_posts = []

    try:
        updates = await bot.get_updates(offset=last_update_id + 1 if last_update_id else None,
                                        allowed_updates=[telegram.Update.MESSAGE], timeout=60)
        logger.info(f"Telegram get_updates response: {updates}") # LOG THE FULL RESPONSE

        for update in updates:
            logger.info(f"Processing update: {update}")  # Log each update
            if update.message and update.message.chat.id == channel_id and update.message.caption:
                new_posts.append(update.message)
            else:
                logger.info(f"Skipping update - Not a message with caption in the correct channel: {update}")
            # Store after *every* processed update
            redis_client.set('last_telegram_update_id', update.update_id)
            logger.info(f"Set last_telegram_update_id to: {update.update_id}")

    except NetworkError as e:
        logger.error(f"Network error fetching Telegram updates: {e}")
        return []
    except RetryAfter as e:
        logger.warning(f"Rate limit exceeded. Retrying after {e.retry_after} seconds.")
        await asyncio.sleep(e.retry_after)
        return []
    except TimedOut as e:
        logger.error(f"Telegram API request timed out: {e}")
        return []
    except Exception as e:
        logger.exception(f"An unexpected error occurred fetching Telegram updates: {e}")
        return []

    logger.info(f"Returning {len(new_posts)} new posts") # Log how many posts
    return new_posts
    def parse_telegram_post(text: str):
    """
    Parses a Telegram post caption to extract show name, season, episode, and download link.

    Args:
        text: The text content of the post's caption.

    Returns:
        A dictionary containing the parsed data, or None if parsing fails.
    """
    if not text:
        return None

    match = re.search(r'^(.*?)\s+(?:S(\d+)E(\d+)|(\d+)x(\d+))\s+-\s+(https?://.*)$', text, re.IGNORECASE)
    if match:
        show_name = match.group(1).strip() if match.group(1) else None
        # Determine which S/E format was used
        if match.group(2) and match.group(3):
            season = int(match.group(2))
            episode = int(match.group(3))
        elif match.group(4) and match.group(5):
            season = int(match.group(4))
            episode = int(match.group(5))
        else:  # Should never happen, but good to be safe
            return None
        download_link = match.group(6).strip() if match.group(6) else None

        if all([show_name, season, episode, download_link]):
            return {
                'show_name': show_name,
                'season': season,
                'episode': episode,
                'download_link': download_link
            }
    return None

@celery.task(bind=True)
def update_tv_shows(self):
    """Fetches new posts from Telegram, parses them, and updates the database."""
    logger.info("Starting update_tv_shows task...")

    lock = redis_client.lock('update_tv_shows_lock', timeout=60)
    if lock.acquire(blocking=False):
        try:
            bot = telegram.Bot(TELEGRAM_BOT_TOKEN)
            new_posts = asyncio.run(fetch_new_telegram_posts(bot))

            if not new_posts:
                logger.info("No new Telegram posts to process.")
                return

            from tv_app.app import app  # Import inside the task
            with app.app_context():
                from tv_app.models import Show, Episode, db  # Import inside app context
                for post in new_posts:
                    # --- Check if message ID has been processed ---
                    if redis_client.sismember("processed_messages", post.message_id):
                        logger.info(f"Message ID {post.message_id} already processed. Skipping.")
                        continue
                    logger.info(f"Post caption: {post.caption}") # Log caption
                    logger.info(f"Caption entities: {post.caption_entities}")  #Log entities

                    post_data = parse_telegram_post(post.caption)
                    if post_data:
                        logger.info(f"Parsed post data: {post_data}")

                        # --- Find or create the show ---
                        show = Show.query.filter_by(name=post_data['show_name']).first()
                        if not show:
                            show = Show(name=post_data['show_name'])
                            db.session.add(show)
                            db.session.flush()  # Get the ID for the new show
                            logger.info(f"Created new show: {show.name} (ID: {show.id})")

                        # --- Create or update the episode ---
                        episode = Episode.query.filter_by(show_id=show.id, season_number=post_data['season'], episode_number=post_data['episode']).first()
                        if episode:
                            # Update existing episode
                            episode.download_link = post_data['download_link']
                            logger.info(f"Updated episode: {show.name} S{post_data['season']}E{post_data['episode']}")
                        else:
                            # Create a new episode
                            episode = Episode(
                                show_id=show.id,
                                season_number=post_data['season'],
                                episode_number=post_data['episode'],
                                download_link=post_data['download_link']
                            )
                            db.session.add(episode)
                            logger.info(f"Created new episode: {show.name} S{post_data['season']}E{post_data['episode']}")

                        # --- Commit changes to the database ---
                        db.session.commit()
                        logger.info(f"Database updated for: {show.name} S{post_data['season']}E{post_data['episode']}")

                        # --- Mark message as processed ---
                        redis_client.sadd("processed_messages", post.message_id)
                    else:
                        logger.warning(f"Could not parse post: {post.caption}")

        except Exception as e:
            logger.exception(f"An unexpected error occurred: {e}")

        finally:
            lock.release()
            logger.info("Lock released.")
    else:
        logger.info("Could not acquire lock, task is likely already running.")


@celery.task
def log_current_time():
    """Logs the current time (for testing)."""
    current_time = redis_client.time()  # Use Redis for accurate time
    dt = datetime.fromtimestamp(current_time[0])  # Convert to datetime
    logger.info(f"Current time according to Celery: {dt.isoformat()}+00:00")
