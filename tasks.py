import os
import re
import logging
from celery import Celery
from bs4 import BeautifulSoup
import requests
from dotenv import load_dotenv
# from app import app, get_all_tv_shows, db  # NO MORE DIRECT app IMPORT
from flask import Flask  # Import Flask here
from models import db, TVShow
from datetime import datetime, timezone


load_dotenv()

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Celery Configuration ---
# Correctly configure Celery using environment variables.
celery = Celery(__name__,
                broker=os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0'),
                backend=os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0'))

# --- Helper Functions (for web scraping) ---
# (These remain unchanged, but I'm including them for completeness)

def extract_episode_info_and_link(link_element):
    """Extracts episode information and download link from a link element."""
    text = link_element.get_text(strip=True) if link_element else "N/A"
    logger.info(f"Extracted text from link: {text}")

    show_name_match = re.search(r'^(.*?)\s+[-–]\s+E(\d+)', text)
    if show_name_match:
        show_name = show_name_match.group(1).strip()
        episode_number = show_name_match.group(2).strip()
    else:
        show_name = text
        episode_number = "N/A"
    download_link = link_element.get('href') if link_element else "N/A"

    episode_title = ""  # Initialize episode_title
    if ".E" in text:
      title_start_index = text.find(".E") + len(".E") + 2
      episode_title = text[title_start_index:].split(" ")[0]
    else:
      episode_title = text

    return show_name, episode_number, episode_title, download_link

def scrape_download_links(url):
    """Scrapes download links from the download page, handling Cloudflare protection."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        soup = BeautifulSoup(response.text, 'html.parser')
        link_elements = soup.find_all('a', href=True)
        return link_elements
    except requests.exceptions.RequestException as e:
        logger.error(f"Error during scraping: {e}")
        return []

# --- Celery Tasks ---

@celery.task(name='tasks.test_task')
def test_task():
    logger.info("Test task executed!")
    return "Test task result"

@celery.task(name='tasks.update_tv_shows')
def update_tv_shows():
    """Fetches new TV show episodes and updates the database."""

    # Create a Flask app context *inside* the task.
    flask_app = Flask(__name__)
    flask_app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key') #Set Config
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    flask_app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(flask_app) #Init db


    with flask_app.app_context():  # Use app_context here
        try:
            link_elements = scrape_download_links('https://torrentsee29.com/topic/index?bo_table=enter')
            logger.info(f"Found {len(link_elements)} link elements.")

            for link_element in link_elements:
                show_name, _, episode_title, download_link = extract_episode_info_and_link(
                    link_element)  # All details
                message_id = int(link_element.get('data-message-id')) if link_element.get(
                    'data-message-id') else None

                if message_id:
                    # Check if the episode already exists
                    existing_show = TVShow.query.filter_by(message_id=message_id).first()

                    if not existing_show:
                        new_show = TVShow(
                            message_id=message_id,
                            show_name=show_name,
                            episode_title=episode_title,
                            download_link=download_link
                        )
                        db.session.add(new_show)
                        db.session.commit()  # Commit *inside* the loop
                        logger.info(
                            f"Added new show: {show_name} - Episode: {episode_title} ({download_link})")
                    else:
                        logger.info(
                            f"Show already exists: {show_name} - Episode: {episode_title}"
                        )


        except Exception as e:
            logger.error(f"An error occurred: {e}")

# --- NO FLASK ROUTES HERE.  This is a Celery worker, not a web app. ---
