# tv_app/init_db.py
from .models import db, TVShow  # Use relative import - CORRECT
from .app import app  # Use relative import - CORRECT

with app.app_context():
    db.create_all()
    print("Database tables created successfully!")
