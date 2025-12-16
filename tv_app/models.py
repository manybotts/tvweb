# tv_app/models.py
from datetime import datetime
import re
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, text, event

db = SQLAlchemy()

# --- M2M association: TVShow <-> Genre ---
show_genres = db.Table(
    "show_genres",
    db.Column("tvshow_id", db.Integer, db.ForeignKey("tv_shows.id"), primary_key=True),
    db.Column("genre_id", db.Integer, db.ForeignKey("genres.id"), primary_key=True),
)

class Genre(db.Model):
    __tablename__ = "genres"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Genre {self.name!r}>"

class TVShow(db.Model):
    __tablename__ = "tv_shows"

    id = db.Column(db.Integer, primary_key=True)

    # UPDATED: Removed unique=True so we can have duplicates across categories
    tmdb_id = db.Column(db.Integer, unique=False, nullable=True, index=True)

    # UPDATED: Removed unique=True (Message 100 in TV != Message 100 in Anime)
    message_id = db.Column(db.BigInteger, unique=False, nullable=False, index=True)

    show_name = db.Column(db.String(255), nullable=False, index=True)
    episode_title = db.Column(db.String(255), default=None)
    download_link = db.Column(db.Text, default=None)

    overview = db.Column(db.Text)
    vote_average = db.Column(db.Float)
    poster_path = db.Column(db.Text, default=None)

    # Required by homepage/trending
    clicks = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    content_hash = db.Column(db.String(64), nullable=False, index=True)

    year = db.Column(db.Integer)
    rating = db.Column(db.Float)

    # Category column (defaults to 'tv')
    category = db.Column(db.String(20), nullable=False, default='tv', index=True)

    # SEO-friendly slug, unique
    slug = db.Column(db.String(255), nullable=False, unique=True, index=True)

    # Many-to-many to Genre
    genres = db.relationship(
        "Genre",
        secondary=show_genres,
        backref=db.backref("tv_shows", lazy="dynamic"),
    )

    __table_args__ = (
        Index("ix_show_name_episode_title", "show_name", "episode_title"),
        # trigram index for Postgres; harmless on SQLite (ignored)
        Index(
            "ix_show_name_trgm",
            "show_name",
            postgresql_using="gin",
            postgresql_ops={"show_name": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<TVShow {self.show_name!r} - {self.episode_title!r}>"

# --- Slug helpers ---
_slug_cleaner = re.compile(r"[^a-z0-9]+")
def _slugify(title: str) -> str:
    s = title.strip().lower()
    s = _slug_cleaner.sub("-", s).strip("-")
    return s or "item"

@event.listens_for(TVShow, "before_insert")
def _ensure_slug(mapper, connection, target: TVShow):
    """Generate a unique slug if missing. Keeps DB from bricking if the task forgets."""
    if target.slug and target.slug.strip():
        base = _slugify(target.slug)
    else:
        parts = [p for p in [target.show_name or "", target.episode_title or ""] if p]
        base = _slugify(" ".join(parts)) or "item"

    slug = base
    # ensure uniqueness at DB level using the same connection
    i = 1
    while True:
        exists = connection.execute(
            text("SELECT 1 FROM tv_shows WHERE slug=:s LIMIT 1"), {"s": slug}
        ).fetchone()
        if not exists:
            break
        i += 1
        slug = f"{base}-{i}"
    target.slug = slug
