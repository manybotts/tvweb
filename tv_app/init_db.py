# tv_app/init_db.py
from .models import db, TVShow  # Use relative import
from .app import app  # Use relative import

with app.app_context():
    db.create_all()
    print("Database tables created successfully!")
