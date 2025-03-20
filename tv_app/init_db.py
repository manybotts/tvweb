# init_db.py
import os
from dotenv import load_dotenv
from tv_app.app import app
from tv_app.models import db, Show, User, Episodes  # Corrected import: Show, not TVShow
from sqlalchemy import inspect

load_dotenv()

def init_db():
    with app.app_context():
        inspector = inspect(db.engine)
        # Check for the existence of *either* table.  If *either* exists, assume
        # the database is initialized.  This is more robust.
        if not (inspector.has_table("tv_shows") or inspector.has_table("episodes")):
            db.create_all()
            print("Database tables created successfully!")

            # Create Admin User (if it doesn't exist)
            try:
                admin = User.query.filter_by(username='admin').first()
                if not admin:
                    admin = User(username='admin')
                    admin.set_password('admin')  # CHANGE THIS IN PRODUCTION!!!
                    db.session.add(admin)
                    db.session.commit()
                    print("Admin user created.")
                else:
                    print("Admin user already exists.")
            except Exception as e:
                db.session.rollback()
                print("Error creating admin user:", e)
        else:
            print("Database tables already exist.")

if __name__ == "__main__":
    init_db()
