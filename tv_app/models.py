# tv_app/models.py
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'

class Show(db.Model):
    __tablename__ = 'tv_shows'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False, index=True)
    overview = db.Column(db.Text)
    release_year = db.Column(db.Integer)
    genre = db.Column(db.Text)  # Comma-separated list of genres
    image_url = db.Column(db.String)
    trailer_url = db.Column(db.String)
    imdb_id = db.Column(db.String) #Keep it as string
    download_link = db.Column(db.String) #This will hold the general show's page link or null
    available_seasons = db.Column(db.Integer)
    is_new = db.Column(db.Boolean, default=False) #is new
    on_slider = db.Column(db.Boolean, default=False) #is on slider
    clicks = db.Column(db.Integer, default=0)  # For tracking popularity

    episodes = db.relationship('Episodes', backref='show', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Show {self.title}>'

class Episodes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255))  # Episode title (can be null)
    episode_number = db.Column(db.Integer, nullable=False)
    season_number = db.Column(db.Integer, nullable=False)
    show_id = db.Column(db.Integer, db.ForeignKey('tv_shows.id'), nullable=False)
    download_link = db.Column(db.String(255)) # Episode-specific download link
    overview = db.Column(db.Text)  # Episode-specific overview
    air_date = db.Column(db.DateTime)

    def __repr__(self):
        return f'<Episode {self.show.title} - S{self.season_number:02d}E{self.episode_number:02d}>'
