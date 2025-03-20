# init_db.py
import os
from dotenv import load_dotenv
from tv_app.models import db, Show, User  # Import your models
from flask import Flask
from sqlalchemy.exc import IntegrityError

load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

def create_initial_data():
    with app.app_context():
        db.create_all()  # Create tables if they don't exist

        # Create an admin user (optional, but good for initial setup)
        try:
            admin = User(username='admin')
            admin.set_password('admin')  # CHANGE THIS PASSWORD IN PRODUCTION!
            db.session.add(admin)
            db.session.commit()
            print("Admin user created.")
        except IntegrityError:
            db.session.rollback()  # User already exists
            print("Admin user already exists.")

        # Example of adding shows (optional, for pre-populating the database)
        try:
            if not Show.query.first():
                #Added shows to the database.
                show1 = Show(show_name = "the handmaid's tale", episode_title = "episode 1", genre="Drama", year = 2023, poster_path ="/path/to/image1.jpg", download_link = "https://example.com/download1")
                show2 = Show(show_name = "the expanse", episode_title = "episode 1", genre="Sci-Fi", year = 2022, poster_path = "/path/to/image2.jpg", download_link = "https://example.com/download2")
                show3 = Show(show_name = "all rise", episode_title ="episode 2",  genre = "Drama", year = 2024, poster_path = "/path/to/image3.jpg", download_link = "https://example.com/download3")
                db.session.add_all([show1, show2, show3])
                db.session.commit()
                print("Initial shows added.")
            else:
                print("Shows already exist in the database.")
        except IntegrityError:
            db.session.rollback()
            print("Error adding initial shows (possibly duplicates).")

if __name__ == '__main__':
    create_initial_data()
    print("Database initialization complete.")
