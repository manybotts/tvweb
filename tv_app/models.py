# tv_app/models.py
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(db.Model, UserMixin):  # UserMixin provides default implementations for Flask-Login
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):  #  representation for debugging
        return f'<User {self.username}>'

class Show(db.Model):
    __tablename__ = 'tv_shows'  # Good practice to specify table name

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String, nullable=True, index=True) # Made nullable.
    show_name = db.Column(db.String, nullable=False, index=True)
    episode_title = db.Column(db.String, default=None)
    download_link = db.Column(db.String, default=None)
    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.String, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    clicks = db.Column(db.Integer, default=0)
    content_hash = db.Column(db.String(64), nullable=True, unique=True, index=True) #Made nullable.
    genre = db.Column(db.Text)  # Added genre, using Text for comma-separated list
    year = db.Column(db.Integer)  # Added year
    season_range = db.Column(db.Text) # Added season_range
    #Relationship
    episodes = db.relationship('Episodes', backref='show', lazy=True) #Relationship

    def __repr__(self):
        return f'<Show {self.show_name} - {self.episode_title}>'

#Added episodes model
class Episodes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255))
    episode_number = db.Column(db.Integer)
    season_number = db.Column(db.Integer)
    show_id = db.Column(db.Integer, db.ForeignKey('tv_shows.id'), nullable=False)
    download_link = db.Column(db.String(255))
    overview = db.Column(db.Text)
    air_date = db.Column(db.DateTime) #Air date

    def __repr__(self):
        return f'<Episode {self.show_id} - S{self.season_number}E{self.episode_number}>'
