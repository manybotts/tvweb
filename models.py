from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()

class TVShow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, unique=True, nullable=False)
    show_name = db.Column(db.String, nullable=False)
    episode_details = db.Column(db.String, nullable=False)
    download_link = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.now(timezone.utc))

    def __repr__(self):
        return f'<TVShow {self.show_name} - {self.episode_details}>'
