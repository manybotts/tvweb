# tv_app/init_db.py
from .models import db, TVShow
from .app import app
from sqlalchemy import inspect

with app.app_context():
    inspector = inspect(db.engine)
    if not inspector.has_table(TVShow.__tablename__):  # Check if table exists
        db.create_all()
        print("Database tables created successfully!")
    else:
        print("Database tables already exist.")
