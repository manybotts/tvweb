# tv_app/init_db.py
from tv_app.app import app # Import the 'app' instance directly
from tv_app.models import db, TVShow  # Import db and TVShow
from sqlalchemy import inspect

with app.app_context():
    inspector = inspect(db.engine)
    if not inspector.has_table(TVShow.__tablename__):
        db.create_all()
        print("Database tables created successfully!")
    else:
        print("Database tables already exist.")
