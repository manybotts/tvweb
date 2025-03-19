# tv_app/models.py
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class TVShow(db.Model):
    __tablename__ = 'tv_shows'

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.String, nullable=False, index=True)
    show_name = db.Column(db.String, nullable=False, index=True)
    episode_title = db.Column(db.String, default=None)
    download_link = db.Column(db.String, default=None)
    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.String, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    clicks = db.Column(db.Integer, default=0)
    content_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    genre = db.Column(db.Text)  # Added genre
    year = db.Column(db.Integer)  # Added year
    season_range = db.Column(db.Text)  # Added season_range

    def __repr__(self):
        return f'<TVShow {self.show_name} - {self.episode_title}>'
