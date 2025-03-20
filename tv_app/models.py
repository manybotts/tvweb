# tv_app/models.py
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func  # Import func

db = SQLAlchemy()

class TVShow(db.Model):
    __tablename__ = 'tv_shows'

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, nullable=False, unique=True, index=True) # Changed to Integer, added unique=True
    show_name = db.Column(db.String, nullable=False, index=True)
    episode_title = db.Column(db.String, default=None)
    download_link = db.Column(db.String, default=None)
    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.String, default=None)
    created_at = db.Column(db.DateTime, server_default=func.now(), index=True)  # Use server_default, add index
    clicks = db.Column(db.Integer, default=0)
    content_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    genre = db.Column(db.Text)
    year = db.Column(db.Integer)  # Keep as Integer
    season_range = db.Column(db.Text)

    def __repr__(self):
        return f'<TVShow {self.show_name} - {self.episode_title}>'
