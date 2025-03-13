#init_db.py
from .app import create_app #Import create_app function
from .models import db, TVShow
from sqlalchemy import inspect

app = create_app() # Call create_app to get the app instance

with app.app_context():
    inspector = inspect(db.engine)
    if not inspector.has_table(TVShow.__tablename__):
        db.create_all()
        print("Database tables created successfully!")
    else:
        print("Database tables already exist.")
