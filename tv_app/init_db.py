# init_db.py
import os
from dotenv import load_dotenv
from tv_app.app import app  # Import the 'app' instance directly
from tv_app.models import db, TVShow, User  # Import db and TVShow
from sqlalchemy import inspect

load_dotenv()

def init_db():
    with app.app_context():
        inspector = inspect(db.engine)
        if not inspector.has_table(TVShow.__tablename__):
            db.create_all()
            print("Database tables created successfully!")

            # --- Create Admin User (if it doesn't exist) ---
            try:
                admin = User(username='admin')
                admin.set_password('admin')  # CHANGE THIS IN PRODUCTION!!!
                db.session.add(admin)
                db.session.commit()
                print("Admin user created.")
            except Exception as e:
                db.session.rollback()
                print("Admin user already exists or error creating it:", e)
        else:
            print("Database tables already exist.")

if __name__ == "__main__":
        init_db()
