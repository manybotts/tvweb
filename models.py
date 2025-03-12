# models.py
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()  # Create the db object *here*

class TVShow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    show_name = db.Column(db.String(255), unique=True, nullable=False)
    season_episode = db.Column(db.String(255))
    download_link = db.Column(db.String(255))
    message_id = db.Column(db.Integer, unique=True)
    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f'<TVShow {self.show_name}>'
