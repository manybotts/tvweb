# init_db.py
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def create_tables():
    """Creates the tv_shows table in the database if it doesn't exist."""

    conn = None
    try:
        # Use DATABASE_URL directly!
        conn = psycopg2.connect(os.environ.get("DATABASE_URL"))
        cur = conn.cursor()

        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tv_shows');")
        table_exists = cur.fetchone()[0]

        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

        if not table_exists:
            cur.execute("""
                CREATE TABLE tv_shows (
                    id SERIAL PRIMARY KEY,
                    message_id INTEGER UNIQUE NOT NULL,
                    show_name VARCHAR NOT NULL,
                    episode_title VARCHAR,
                    download_link VARCHAR,
                    overview TEXT,
                    vote_average FLOAT,
                    poster_path VARCHAR,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    clicks INTEGER DEFAULT 0,
                    content_hash VARCHAR(64) NOT NULL
                );
            """)
            cur.execute("CREATE INDEX ix_tv_shows_message_id ON tv_shows (message_id);")
            cur.execute("CREATE INDEX ix_tv_shows_show_name ON tv_shows (show_name);")
            cur.execute("CREATE INDEX ix_tv_shows_created_at ON tv_shows (created_at);")
            cur.execute("CREATE INDEX ix_tv_shows_content_hash ON tv_shows (content_hash);")
            cur.execute("CREATE INDEX ix_show_name_episode_title ON tv_shows (show_name, episode_title);")
            cur.execute("CREATE INDEX ix_show_name_trgm ON tv_shows USING gin (show_name gin_trgm_ops);")

            conn.commit()
            print("Table 'tv_shows' created successfully (if it didn't exist)!")

    except (Exception, psycopg2.DatabaseError) as error:
        print(f"Error creating table: {error}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            cur.close()
            conn.close()

if __name__ == '__main__':
    create_tables()
