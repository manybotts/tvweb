# tv_app/models.py
from tv_app import db  # Import the db object from __init__.py
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import datetime

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))  # CORRECTED: Increased length

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):  # Added for easier debugging
        return f"<User(username='{self.username}')>"

class Show(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False, index=True)
    overview = db.Column(db.Text, nullable=True)
    release_year = db.Column(db.Integer, nullable=True)
    genre = db.Column(db.String(255), nullable=True)
    image_url = db.Column(db.String(255), nullable=True)
    trailer_url = db.Column(db.String(255), nullable=True)
    imdb_id = db.Column(db.String(255), nullable=True)
    download_link = db.Column(db.String(255), nullable=True)  # Global download link (optional)
    available_seasons = db.Column(db.Integer, default=1)
    clicks = db.Column(db.Integer, default=0) #For tracking the popularity
    episodes = db.relationship('Episodes', backref='show', lazy=True, cascade="all, delete-orphan")
    is_new = db.Column(db.Boolean, default=False)
    on_slider = db.Column(db.Boolean, default=False)  # For the slider

    def __repr__(self):  # Added for easier debugging
        return f"<Show(title='{self.title}')>"


class Episodes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=True) #Episode title, null if not
    episode_number = db.Column(db.Integer, nullable=False)
    season_number = db.Column(db.Integer, nullable=False)
    show_id = db.Column(db.Integer, db.ForeignKey('show.id'), nullable=False)
    download_link = db.Column(db.String(255), nullable=False) #Episode download link
    overview = db.Column(db.Text, nullable=True)
    added_date = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def __repr__(self):  # Added for easier debugging
        return f"<Episode(show_id={self.show_id}, season={self.season_number}, episode={self.episode_number})>"
