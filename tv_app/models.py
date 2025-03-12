# models.py
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()  # Create the db object here

class TVShow(db.Model):
    __tablename__ = 'tv_shows'

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, unique=True, nullable=False)
    show_name = db.Column(db.String, nullable=False)
    episode_title = db.Column(db.String)  # USE THIS FIELD
    download_link = db.Column(db.String)
    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<TVShow {self.show_name} - {self.episode_title}>' #USE episode_title here
