from celery import Celery
from celery.exceptions import MaxRetriesExceededError
import os
import re
import requests
from urllib.parse import quote_plus
import logging
from dotenv import load_dotenv
import asyncio
from pyrogram import Client, errors
import difflib
import json

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

celery = Celery(__name__, broker=os.environ.get('REDIS_URL'), backend=os.environ.get('REDIS_URL'))
celery.conf.timezone = 'UTC'  # Use UTC
celery.conf.enable_utc = True
celery.conf.beat_schedule = {
    'update-tv-shows-every-15-minutes': {  # Change back to 15 after testing!
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': 15*60,  # Every 15 minutes
    },
}

CALLS = 30
PERIOD = 9
DATABASE_BATCH_SIZE = 10

api_id = int(os.environ.get("API_ID"))  # Ensure these are set in Railway
api_hash = os.environ.get("API_HASH")
bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
channel_id = int(os.environ.get('TELEGRAM_CHANNEL_ID'))  # Convert to integer.

# Pyrogram Client (Global Instance)
pyrogram_client = Client("tv_shows_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)

# Initialization Flag File
INIT_FLAG_FILE = "/tmp/telegram_bot_initialized"  # Use /tmp for Railway

async def initialize_telegram_bot():
    """Initializes the Telegram bot and sends a test message (ONCE)."""
    # Check if the initialization has already been done, or if forced.
    force_init = os.environ.get("INIT_FORCE_RESET", "False").lower() == "true"
    if not os.path.exists(INIT_FLAG_FILE) or force_init:
        logger.info("Initializing Telegram bot connection...")
        try:
            async with pyrogram_client:  # Start and stop client
                # Get the chat object. This confirms we can access the channel.
                try:
                    chat = await pyrogram_client.get_chat(channel_id)
                    if chat.type != "channel":  # More specific check
                        logger.error(f"The provided ID {channel_id} is not a channel.")
                        return False  # Don't proceed if it's not a channel
                except errors.PeerIdInvalid:
                    logger.error(f"Bot has never interacted with channel ID {channel_id}.  You MUST add the bot as an ADMIN with POSTING rights.")
                    return False
                except errors.ChatAdminRequired:
                    logger.error(f"Bot is not an admin in channel {channel_id}.  Make it an admin.")
                    return False
                except Exception as e:
                     logger.error(f"Bot could not connect, check the provided variables: {e}")
                     return False


                # Send a test message (and immediately delete it).  This creates
                # the necessary initial interaction.
                #message = await pyrogram_client.send_message(chat_id=channel_id, text="Bot initializing...")
                #await pyrogram_client.delete_messages(chat_id=channel_id, message_ids=message.id)

                logger.info("Telegram bot initialized successfully.")
                with open(INIT_FLAG_FILE, "w") as f:
                    f.write("initialized")  # Create the flag file
                return True  # Initialization successful

        except Exception as e:
            logger.exception(f"Unexpected error during initialization: {e}")
            return False  # Initialization failed
    else:
        logger.info("Telegram bot already initialized.")
        return True # Initialization not needed

async def fetch_telegram_posts():
    logger.info(f"Fetching updates from Telegram channel: {channel_id}")
    posts = []
    try:
        async with pyrogram_client:
          async for message in pyrogram_client.get_chat_history(chat_id=channel_id):
            if message.caption:
                posts.append(message)
                logger.debug(f"Added post to processing list: {message.id}")

    except errors.FloodWait as e:
        logger.warning(f"FloodWait error: {e}.  Waiting for {e.value} seconds.")
        await asyncio.sleep(e.value)
        posts.extend(await fetch_telegram_posts())
    except errors.ChatAdminRequired:
        logger.error(f"Bot is not an admin in {channel_id}.  Add it as admin with POSTING rights.")
        return []
    except errors.PeerIdInvalid:
        logger.error(f"Peer ID invalid ({channel_id}). Bot may not have interacted with channel or ID is wrong.")
        return []
    except Exception as e:
        logger.exception(f"Error fetching posts: {e}")
        return []
    logger.info(f"Total posts fetched: {len(posts)}")
    return posts

def parse_telegram_post(post):
    """Parses a Telegram post caption."""
    try:
        text = post.caption
        logger.debug(f"Parsing post: {post.id}, Caption: {text!r}")
        lines = text.splitlines()
        show_name = None
        season_episode = None
        download_link = None

        if len(lines) >= 1:
            show_name = lines[0].strip()
            logger.debug(f"Show Name: {show_name}")
        if len(lines) > 1:
            if lines[1].strip().startswith('#_'):
                season_episode = None
                logger.debug("Season/Episode: None (starts with #_)")
            else:
                season_episode = lines[1].strip()
                logger.debug(f"Season/Episode: {season_episode}")
        # Find the download link by iterating through entities
        if post.caption_entities:
          for entity in post.caption_entities:
              if entity.type == 'text_link':
                 download_link = entity.url
                 logger.info(f"Download Link Found: {download_link}")
                 break
        if show_name:
          show_name = preprocess_show_name(show_name) #clean the show name
          return {
                'show_name': show_name,
                'season_episode': season_episode,
                'download_link': download_link,
                'message_id': post.id,  # Use Pyrogram's message ID
            }
        else:
            logger.warning(f"No show name found in post: {post.id}")
            return None

    except Exception as e:
        logger.exception(f"Error during parsing: {e}")
        return None

def preprocess_show_name(name):
    """Cleans up the show name before querying TMDb."""
    # Remove common extra text (case-insensitive)
    name = re.sub(r"(?i)\s*(season finale|new episodes|original series|tv series|limited series)\s*", "", name)
    # Remove trailing years (e.g., "Show Name 2023")
    name = re.sub(r"\s*\(\d{4}\)$", "", name)  # (YYYY) at the end
    name = re.sub(r"\s*\d{4}$", "", name)       # Year at the end
    # Replace "&" with "and" and vice-versa
    name = name.replace("&", "and").replace("  ", " ")
     # Remove any brackets
    name = re.sub(r'[\(\[].*?[\)\]]', '', name)
     # Remove common short forms (case-insensitive)
    name = re.sub(r"(?i)\s*\b(hd|4k|2k|fhd|s\d+|e\d+)\b", "", name) #Removes any short form word.

    return name.strip()

def get_close_matches_with_threshold(query, possibilities, n=3, cutoff=0.6):
    """Find close matches to a query string."""
    return difflib.get_close_matches(query, possibilities, n=n, cutoff=cutoff)

# --- Rate Limited TMDB Fetch ---
#@sleep_and_retry
#@limits(calls=CALLS, period=PERIOD) # Removed
def fetch_tmdb_data(show_name, language='en-US'):
   #Rest of the code


@celery.task
def update_tv_shows():
    """Updates the database with new TV show info from Telegram."""
    logger.info("Starting update_tv_shows task.")

    try:
        posts = asyncio.run(fetch_telegram_posts())  # Use asyncio.run
        if not posts:
            logger.info("No new posts found.")
            return

        from tv_app.app import app  # Correct relative import
        with app.app_context():
            from tv_app.models import db, TVShow  # Correct relative import

            for post in posts:
                parsed_data = parse_telegram_post(post)
                if parsed_data:
                    logger.info(f"Processing show: {parsed_data['show_name']}")
                    tmdb_data = fetch_tmdb_data(parsed_data['show_name']) # Call directly

                    show_data = {
                        'show_name': parsed_data['show_name'],
                        'episode_title': parsed_data['season_episode'],
                        'download_link': parsed_data['download_link'],
                        'message_id': parsed_data['message_id'],
                        'overview': tmdb_data.get('overview') if tmdb_data else None,
                        'vote_average': tmdb_data.get('vote_average') if tmdb_data else None,
                        'poster_path': tmdb_data.get('poster_path') if tmdb_data else None,
                    }

                    existing_show = TVShow.query.filter_by(message_id=parsed_data['message_id']).first()
                    if existing_show:
                        for key, value in show_data.items():
                            setattr(existing_show, key, value)
                        logger.info(f"Successfully updated: {parsed_data['show_name']}")
                    else:
                        new_show = TVShow(**show_data)  # Use ** to unpack
                        db.session.add(new_show)
                        logger.info(f"Successfully inserted: {parsed_data['show_name']}")

                db.session.commit() # Commit outside the loop
                db.session.remove() # Close the connection

    except Exception as e:
        logger.exception(f"An unexpected error occurred in update_tv_shows: {e}")
        # No retry for now. Let's get it working first.
