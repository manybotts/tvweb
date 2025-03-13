# app.py
import os
import re
from flask import Flask, render_template, redirect, url_for, request
import logging
from dotenv import load_dotenv
from .models import db, TVShow
from sqlalchemy import desc
from .tasks import make_celery


load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['REDIS_URL'] = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # For Celery
    db.init_app(app)

    # Create Celery instance *after* app, using make_celery
    celery = make_celery(app)


    # --- Routes ---
    from . import routes  # Import routes *inside* create_app
    app.register_blueprint(routes.bp) #Register blueprint

    return app
