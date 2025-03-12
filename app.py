import os
from flask import Flask, render_template, redirect, url_for, request
import logging
from dotenv import load_dotenv
from tasks import update_tv_shows  # Import Celery task
from models import db, TVShow

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your_secret_key')

# --- CORRECT DATABASE CONFIGURATION (Simplified) ---
# Use the DATABASE_URL environment variable provided automatically by Railway.
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Suppress a warning

db.init_app(app)  # Initialize db with the app

# Create tables within the application context
with app.app_context():
    db.create_all()
    logger.info("SQLAlchemy and PostgreSQL Database connected")

# --- Database Operations (Rest of your app.py code remains the same) ---
# ... (get_all_tv_shows, get_tv_show_by_message_id, etc.) ...
# --- Routes (Rest of your app.py code remains the same) ---
# ... (@app.route, etc.) ...
if __name__ == "__main__":
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
