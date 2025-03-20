# tv_app/models.py
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class TVShow(db.Model):
    __tablename__ = 'tv_shows'

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, unique=True, nullable=False, index=True)
    show_name = db.Column(db.String, nullable=False, index=True)
    episode_title = db.Column(db.String, default=None)
    download_link = db.Column(db.String, default=None)
    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.String, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    clicks = db.Column(db.Integer, default=0)
    content_hash = db.Column(db.String(64), nullable=False, index=True)

    __table_args__ = (
        db.Index('ix_show_name_episode_title', 'show_name', 'episode_title'),
        db.Index('ix_show_name_trgm', 'show_name', postgresql_using='gin', postgresql_ops={'show_name': 'gin_trgm_ops'}),
    )

    def __repr__(self):
        return f'<TVShow {self.show_name} - {self.episode_title}>'
