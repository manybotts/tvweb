DROP TABLE IF EXISTS tv_shows;

CREATE TABLE tv_shows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  show_name TEXT UNIQUE NOT NULL,
  season_episode TEXT,
  download_link TEXT,
  message_id INTEGER,
  overview TEXT,
  vote_average REAL,
  poster_path TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_show_name ON tv_shows(show_name);
CREATE INDEX IF NOT EXISTS idx_message_id ON tv_shows(message_id);
