# tv_app/init_db.py
from models import db, TVShow  # Import your models
from app import app  # Import your Flask app

with app.app_context():
    db.create_all()
    print("Database tables created successfully!")
