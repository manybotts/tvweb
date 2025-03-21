# TV Show Tracking Application

This application allows users to track TV shows, receive notifications about new episodes, and manage a database of show information.  It uses Flask for the web framework, Celery for asynchronous task management, PostgreSQL for the database, and Redis for caching and as a Celery broker. It also integrates with Telegram for notifications and TMDb for show metadata

## Project Structure

tv_show_project/
├── tv_app/               # Main application package
│   ├── __init__.py       # Makes tv_app a package
│   ├── app.py          # Flask application and routes
│   ├── models.py       # Database models
│   ├── tasks.py        # Celery tasks
│   ├── static/
│   │   ├── style.css
│   │   ├── script.js
│   │   ├── logo.png
│   │   └── favicon.ico
│   └── templates/
│       ├── base.html
│       ├── index.html
│       ├── show_details.html
│       ├── shows.html
│       ├── 404.html
│       └── 500.html
├── celeryconfig.py    # Celery configuration
├── requirements.txt   # Python dependencies
├── .env               # Environment variables (NOT committed to Git)
└── Procfile           # Railway/Heroku deployment file

## File Descriptions

*   **`tv_app/app.py`:** The main Flask application file.  It defines the web routes, handles user interactions (search, show details, redirects), and interacts with the database. It uses the `pg_trgm` extension for fuzzy searching. It *does not* directly import Celery tasks at the top level to avoid circular imports. Instead it calls them using the Celery API.

*   **`tv_app/celeryconfig.py`:**  Configures Celery.  Specifies the broker URL (Redis), result backend (also Redis), and the Celery Beat schedule for periodic tasks. *Critically*, it uses the correct task paths (`tv_app.tasks.update_tv_shows`, `tv_app.tasks.reset_clicks`) for Celery to find the tasks.

*   **`tv_app/models.py`:**  Defines the database models using SQLAlchemy: `TVShow` (stores show information, including message ID, download link, clicks, etc.) and `Genre` (stores genre names, with a many-to-many relationship to `TVShow`). It *includes* the `pg_trgm` index for efficient fuzzy searching on the `show_name` column.  It instantiates the `db` object directly, as you are *not* using the application factory pattern.

*   **`tv_app/tasks.py`:**  Contains the Celery tasks:
    *   **`update_tv_shows`:** Fetches new Telegram posts, parses them, fetches TMDb data, and updates (or creates) TV show entries in the database. Uses the application context (`with app.app_context():`) for database access, and performs *local* imports of `app`, `db`, `TVShow`, and `Genre` *within* the `with` block. It is designed to handle both new posts and edits to existing posts, preventing duplicates.
    *   **`reset_clicks`:** Resets the `clicks` count for all TV shows to 0. Also uses the application context for database access and local imports.
    *   **`test_task`:** A simple task to verify Celery is working. (You can remove this in production if you wish).

*   **`tv_app/static/`:**  Contains static assets.
    *   **`css/style.css`:**  CSS styles for the application.
    *   **`images/youcine.jpg`:** An image.
    *   **`script.js`:** Contains Javascript for fronted functionalities.

*   **`tv_app/templates/`:**  Contains the Jinja2 templates for the web pages.
    *   **`base.html`:**  The base template that other templates inherit from.  Defines the overall layout and includes the Plausible Analytics script.
    *   **`index.html`:**  The main page, displaying recently added shows, trending shows (based on clicks), and a search bar.
    *   **`show_details.html`:** Displays details for a specific TV show (fetched from the database), including the download link.  Handles incrementing the click count.
    *   **`shows.html`:** A page to list shows with filter.
    *   **`404.html`:** Custom 404 error page.
    *   **`500.html`:** Custom 500 error page.

*   **`tv_app/__init__.py`:**  An empty file that makes the `tv_app` directory a Python package.

*  **`tv_app/forms.py`:**  Currently empty.  This file can be used to define Flask-WTF forms if needed in the future. If not used, it can be safely removed.
*  ## Setup and Installation

1.  Clone the repository:
    git clone <your_repository_url>
    cd <your_repository_name>
    

2.  Create a virtual environment (recommended):
    python3 -m venv venv
    source venv/bin/activate  # On Linux/macOS
    venv\Scripts\activate  # On Windows

3.  Install dependencies:
    pip install -r requirements.txt

4.  Set environment variables:
    Create a .env file and add:
    DATABASE_URL=postgresql://user:password@host:port/database
    REDIS_URL=redis://:password@host:port/0
    TMDB_BEARER_TOKEN=your_tmdb_bearer_token
    TELEGRAM_BOT_TOKEN=your_telegram_bot_token
    TELEGRAM_CHANNEL_ID=your_telegram_channel_id
    SECRET_KEY=your_flask_secret_key

5.  Initialize the database:
    python -m tv_app.init_db
    

   Alternatively, you can use Flask-Migrate:
      flask db init
      flask db migrate -m "Initial Migration"
      flask db upgrade
## Running the Application

### Locally

1.  Start the Flask web server (combined command):
    python -m tv_app.init_db && gunicorn "tv_app.app:app" --workers 1 --bind 0.0.0.0:$PORT

    *Explanation:* This runs the init_db.py script, then starts Gunicorn
    with one worker, binding to all interfaces on the port from $PORT
    (defaulting to 5000 if $PORT is not set).

2.  Start the Celery worker (separate terminal):
    celery -A tv_app.tasks worker -l info -c 1 -B

    *Explanation:* Starts a Celery worker.
    -A tv_app.tasks: Specifies the Celery application.
    -l info: Sets logging level.
    -c 1: Limits concurrency to 1 worker process.
    -B: Starts Celery Beat (for periodic tasks).
### On Railway

1.  Create a Railway project.
2.  Connect your GitHub repository.
3.  Add the necessary services:
    *   PostgreSQL
    *   Redis
4.  Set the environment variables (as listed in the Setup section) in the
    Railway project settings. *Do not* commit your .env file.
5. Set the Start Command for your web service:
   `python -m tv_app.init_db && gunicorn "tv_app.app:app" --workers 1 --bind 0.0.0.0:$PORT`
6.  Deploy. Railway should automatically detect the Procfile and start
    the web and worker processes.  The Procfile should contain:

    web: gunicorn "tv_app.app:app" --workers 1 --bind 0.0.0.0:$PORT
    worker: celery -A tv_app.tasks worker -l info -c 1 -B
## Features

*   TV Show Tracking: Automatically fetches and stores show data.
*   TMDb Integration: Uses TMDb for missing info, posters, and ratings.
*   Fuzzy Search: Uses pg_trgm for efficient fuzzy searching.
*   Click Tracking: Tracks show detail views.
*   Trending Shows: Displays most-clicked shows.
*   Periodic Tasks (Celery Beat):
    *   update_tv_shows: Runs every 15 minutes.
    *   reset_clicks: Runs daily at midnight.
*   Show Filtering and Sorting: Filter by genre, rating, year, and sort.
*   Pagination: List of shows is paginated.
*   Show Details Page: Displays show information.
*   ## Important Notes

*   Circular Imports: Avoided by local imports within task functions.
*   Concurrency: Celery worker uses -c 1; Gunicorn uses --workers 1.
*   pg_trgm Extension: Required for fuzzy searching.
*   Telegram API: Requires a Telegram bot token and chat ID.
*   TMDb API: Requires a TMDb bearer token.
*   init_db.py Script: Initializes the database on each deployment.
*   
