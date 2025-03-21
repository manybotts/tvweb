# tv_app/models.py
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import Index  # Import Index

db = SQLAlchemy()

# --- Association Table for Many-to-Many Relationship (TVShow <-> Genre) ---
show_genres = db.Table('show_genres',
    db.Column('tvshow_id', db.Integer, db.ForeignKey('tv_shows.id'), primary_key=True),
    db.Column('genre_id', db.Integer, db.ForeignKey('genres.id'), primary_key=True)
)

class Genre(db.Model):
    __tablename__ = 'genres'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # e.g., "Action", "Comedy"

    def __repr__(self):
        return f'<Genre {self.name}>'

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
    year = db.Column(db.Integer)  # Add release year
    rating = db.Column(db.Float) # Add rating

    # Relationship to Genre (Many-to-Many)
    genres = db.relationship('Genre', secondary=show_genres, backref=db.backref('tv_shows', lazy='dynamic'))


    __table_args__ = (
        Index('ix_show_name_episode_title', 'show_name', 'episode_title'),
        Index('ix_show_name_trgm', 'show_name', postgresql_using='gin', postgresql_ops={'show_name': 'gin_trgm_ops'}),
    )

    def __repr__(self):
        return f'<TVShow {self.show_name} - {self.episode_title}>'
