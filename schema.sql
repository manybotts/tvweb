DROP TABLE IF EXISTS tv_shows;
CREATE TABLE tv_shows (
  message_id INTEGER PRIMARY KEY,
  show_name TEXT NOT NULL,
  season_episode TEXT,
  download_link TEXT,
  poster_path TEXT,
  overview TEXT,
  vote_average REAL
);
