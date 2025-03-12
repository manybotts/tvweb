# TV Show Tracker

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/template?template=YOUR_GITHUB_REPO_URL)
[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=YOUR_GITHUB_REPO_URL)
This Flask application tracks TV show updates from a Telegram channel, displays them on a website, and allows users to search and view show details. It uses Celery for asynchronous task processing (fetching and parsing Telegram posts) and a PostgreSQL database.

## Features

*   **Telegram Integration:** Automatically fetches new TV show postings from a specified Telegram channel.
*   **TMDb API Integration:** Retrieves show details (poster, overview, rating) from The Movie Database (TMDb).
*   **Database Storage:** Stores show information in a PostgreSQL database.
*   **Web Interface:**
    *   Displays a paginated list of TV shows.
    *   Allows searching for shows by name.
    *   Provides a details page for each show.
    *   Includes a "Download" button linking to the original Telegram post (or a provided download link).
*   **Celery Worker:** Handles background tasks for fetching and processing Telegram updates.
*	**Redis Integration:** Celery worker gets the needed data from redis

## Prerequisites

Before deploying, you'll need:

*   A Telegram bot token and channel ID.  Create a Telegram bot using BotFather and obtain its token.  Get the channel ID of the Telegram channel you want to monitor (you may need to use a separate bot or tool to find the channel ID if it's a private channel).
*   A TMDb API bearer token.  Sign up for a free TMDb API key at [https://www.themoviedb.org/](https://www.themoviedb.org/). You will need the "API Read Access Token" (bearer token).
*   Python 3.9+ installed locally (for testing).
*   Git installed.
*   Accounts on Railway, Heroku, and/or Koyeb.

## Local Development (Optional)

1.  **Clone the repository:**

    ```bash
    git clone YOUR_GITHUB_REPO_URL
    cd YOUR_PROJECT_DIRECTORY
    ```

2.  **Create a virtual environment (recommended):**

    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    ```

3.  **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Create a `.env` file:** In the project's root directory (next to `requirements.txt`), create a file named `.env` and add the following, replacing the placeholders with your actual credentials:

    ```
    SECRET_KEY=your_secret_key_here
    TELEGRAM_BOT_TOKEN=your_telegram_bot_token
    TELEGRAM_CHANNEL_ID=-your_telegram_channel_id
    TMDB_BEARER_TOKEN=your_tmdb_bearer_token
    DATABASE_URL=postgresql://user:password@localhost:5432/your_local_db_name
    REDIS_URL=redis://localhost:6379/0
    ```
     *  `DATABASE_URL`:  For local development, you can use a local PostgreSQL database.  Replace `user`, `password`, and `your_local_db_name` with your local database credentials.
      * `REDIS_URL`: Set redis url

5.  **Initialize the database (locally):**

    ```bash
    flask db init
    flask db migrate -m "Initial migration"
    flask db upgrade
    ```
    Or for a shortcut:

    ```bash
     python -c "from tv_app.models import db; from tv_app.app import app;  with app.app_context(): db.create_all()"
    ```
6. **Start Redis Server:**
Start your redis server in a separate terminal
    ```bash
    redis-server
    ```
7.  **Run the Flask app (locally):**

    ```bash
    flask run
    ```

8.  **Run the Celery worker (in a separate terminal):**

    ```bash
    celery -A tv_app.tasks.celery worker -l info
    ```

    The app will be accessible at `http://127.0.0.1:5000` (or the port specified).

## Deployment

### Railway (Recommended)

1.  **Create a new Railway project:**  Go to [https://railway.app/](https://railway.app/) and create a new project.
2.  **Connect to your GitHub repository:**  Link your Railway project to the GitHub repository containing your Flask application.
3.  **Add Services:**
    *   Add a **PostgreSQL** service. Railway will automatically provide the `DATABASE_URL` environment variable.
    *   Add a **Redis** service.  Railway will automatically provide the `REDIS_URL` environment variable.
4.  **Configure Environment Variables:** In your Railway project's settings, add the following environment variables (under "Variables"):
    *   `SECRET_KEY`:  A strong, secret key (generate a random one).
    *   `TELEGRAM_BOT_TOKEN`:  Your Telegram bot token.
    *   `TELEGRAM_CHANNEL_ID`:  Your Telegram channel ID (as a string, including the `-`).
    *   `TMDB_BEARER_TOKEN`: Your TMDb API bearer token.
    *   `PYTHON_VERSION`: `3.9.x` (or your desired Python version, matching your `runtime.txt`)

5.  **Configure Services**
    *   **Web Service:**
        *   Set the **start command** to: `gunicorn "tv_app.app:app" --workers 4 --bind 0.0.0.0:$PORT`
        *   Set the **root directory** to  `tv_show_tracker`
    *   **Worker Service:**
        *   Create a *new* service for the Celery worker.
        *   Set the **start command** to: `celery -A tv_app.tasks.celery worker -l info`
        *   Set the **root directory** to  `tv_show_tracker`
6.  **Deploy:** Railway should automatically deploy your application.  Make sure both the web service *and* the worker service are running.
7. **Create database tables:** In the Railway console (or a local terminal connected to the Railway project), connect to the database shell using the following command
```bash
python -c "from tv_app.models import db; from tv_app.app import app;  with app.app_context(): db.create_all()"
