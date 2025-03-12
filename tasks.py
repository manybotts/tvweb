import os
import re  # Import the regular expression module
import telegram
from telegram.error import TelegramError
import logging
import time
from celery import Celery
from celery.schedules import crontab

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Celery
celery = Celery(__name__, broker=os.environ.get('REDIS_URL', 'redis://localhost:6379/0'))

@celery.task(bind=True, retry_backoff=True)
def update_tv_shows(self):
    """Fetches new posts from a Telegram channel and updates the database."""
    lock_acquired = False  # Initialize lock status
    try:
        # Attempt to acquire a lock (using Redis for simplicity)
        from app import app
        with app.app_context():
          from models import db, TVShow
          lock = db.session.query(TVShow).filter_by(show_name='_lock').with_for_update(nowait=True).first()
          if lock:
              logger.info("Another task is already running. Exiting.")
              return  # Exit if another task is running
          else:
              # Create a placeholder record to act as a lock
              lock = TVShow(message_id=-1, show_name='_lock', episode_details="N/A", download_link="N/A")
              db.session.add(lock)
              db.session.commit()
              lock_acquired = True  # Set lock status to True
              logger.info("Lock acquired. Proceeding with the task.")
        # Fetch new posts from Telegram
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        channel_id = os.environ.get('TELEGRAM_CHANNEL_ID')
        if not bot_token or not channel_id:
          logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set.")
          return
        bot = telegram.Bot(token=bot_token)
        try:
            updates = bot.get_updates(allowed_updates=[telegram.Update.CHANNEL_POST])
        except TelegramError as e:
            logger.error(f"Error fetching updates from Telegram: {e}")
            return
        logger.info(f"Fetched {len(updates)} updates from Telegram.")
        # Process new posts
        with app.app_context():
            from models import db, TVShow  # Import INSIDE app_context
            for update in updates:
                if update.channel_post:
                    message_id = update.channel_post.message_id
                    text = update.channel_post.text
                    if text:
                        # Parse post data (show name, episode, link)
                        show_data = parse_post_data(text)
                        if show_data:
                            logger.info(f"Parsed show data: {show_data}")
                            # Check if the show already exists
                            existing_show = TVShow.query.filter_by(message_id=message_id).first()
                            # Add the show to the database if it doesn't exist
                            if not existing_show:
                                new_show = TVShow(
                                    message_id=message_id,
                                    show_name=show_data['show_name'],
                                    episode_details=show_data['episode_details'],
                                    download_link=show_data['download_link']
                                )
                                db.session.add(new_show)
                                db.session.commit()
                                logger.info(f"Added new show: {show_data['show_name']} - {show_data['episode_details']}")
                            else:
                                logger.info(f"Show already exists: {show_data['show_name']} - {show_data['episode_details']}")
    except Exception as e:
      logger.exception(f"An error occurred: {e}")
    finally:
        # Release the lock
      if lock_acquired:
        from app import app
        with app.app_context():
          from models import db, TVShow
          lock = db.session.query(TVShow).filter_by(show_name='_lock').first()
          if lock:
            db.session.delete(lock)
            db.session.commit()
            logger.info("Lock released.")
          else:
              logger.warning("Lock record not found for deletion!")

def parse_post_data(text):
    """Parses a Telegram post text to extract show name, episode details, and download link."""
    # Regular expressions for extracting show information
    show_name_pattern = re.compile(r'(.+?)(?:S\d+E\d+|$)', re.IGNORECASE)
    episode_details_pattern = re.compile(r'(S\d+E\d+)', re.IGNORECASE)
    download_link_pattern = re.compile(r'(https?://[^\s]+)', re.IGNORECASE)
    # Extract show name, episode details, and download link using regex
    show_name_match = show_name_pattern.search(text)
    episode_details_match = episode_details_pattern.search(text)
    download_link_match = download_link_pattern.search(text)
    # Prepare the result dictionary
    if show_name_match and episode_details_match and download_link_match:
        return {
            'show_name': show_name_match.group(1).strip(),
            'episode_details': episode_details_match.group(1).strip(),
            'download_link': download_link_match.group(1).strip()
        }
    else:
        # Log unexpected post format
        logger.warning(f"Unexpected post format: {text}")
        return None  # Return None if parsing fails
