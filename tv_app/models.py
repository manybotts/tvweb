# tv_app/models.py
from . import db  # Import db from the same package (__init__.py)
from flask_login import UserMixin  # Import UserMixin
from sqlalchemy import event
from sqlalchemy.engine import Engine
import logging
# Configure logging at the top of the file
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

#Added UserMixin for login
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False) #Consider hashing
    email = db.Column(db.String(120), unique=True, nullable=False)

    def __repr__(self):
        return f'<User {self.username}>'

class Show(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    overview = db.Column(db.Text, nullable=True)
    release_year = db.Column(db.Integer, nullable=True)
    genre = db.Column(db.String(100), nullable=True)
    image_url = db.Column(db.String(255), nullable=True)
    trailer_url = db.Column(db.String(255), nullable=True)
    imdb_id = db.Column(db.String(20), nullable=True)
    available_seasons = db.Column(db.Integer, nullable=True)
    clicks = db.Column(db.Integer, default=0)
    is_new = db.Column(db.Boolean, default=True)
    on_slider = db.Column(db.Boolean, default=False)
    episodes = db.relationship('Episodes', backref='show', lazy=True)

    def __repr__(self):
        return f'<Show {self.title}>'


class Episodes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    show_id = db.Column(db.Integer, db.ForeignKey('show.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)  # Make title non-nullable
    season_number = db.Column(db.Integer, nullable=False)
    episode_number = db.Column(db.Integer, nullable=False)
    download_link = db.Column(db.String(255), nullable=True)
    # show = db.relationship('Show', backref=db.backref('episodes', lazy=True)) #Removed, using backref

    def __repr__(self):
        return f'<Episode S{self.season_number}E{self.episode_number} of {self.show.title}>' #Use backref
