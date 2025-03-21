# TV Show Tracking Application

This application allows users to track TV shows, receive notifications about new episodes, and manage a database of show information.  It uses Flask for the web framework, Celery for asynchronous task management, PostgreSQL for the database, and Redis for caching and as a Celery broker. It also integrates with Telegram for notifications and TMDb for show metadata.

## Project Structure

The project has the following directory structure:



## File Descriptions

*   **`app.py`:** The main Flask application file.  It defines the web routes, handles user interactions (search, show details, redirects), and interacts with the database.  It *does not* directly import Celery tasks at the top level to prevent circular imports. It includes routes like `/` (index), `/show/<int:show_id>` (show details), `/redirect/<int:show_id>` (redirect to download), `/shows` (filterable show list), `/update` (triggers Celery task), `/test_celery`, and `/delete_all`.
*   **`celeryconfig.py`:**  Configures Celery.  Specifies the broker URL (Redis), result backend (also Redis), and the Celery Beat schedule for periodic tasks. *Critically*, it uses the correct task paths (`tv_app.tasks.update_tv_shows`, `tv_app.tasks.reset_clicks`) for Celery to find the tasks.
*   **`forms.py`:**  Currently empty.  This file can be used to define Flask-WTF forms if needed in the future. If not used, it can be safely removed.
*   **`models.py`:**  Defines the database models using SQLAlchemy: `TVShow` (stores show information, including message ID, download link, clicks, etc.) and `Genre` (stores genre names, with a many-to-many relationship to `TVShow`). It *must* include the `pg_trgm` index for efficient fuzzy searching.
*   **`static/`:**  Contains static assets.
    *   **`css/style.css`:**  CSS styles for the application.
    *   **`images/youcine.jpg`:** An image.
*   **`tasks.py`:**  Contains the Celery tasks:
    *   **`update_tv_shows`:** Fetches new Telegram posts, parses them, fetches TMDb data, and updates (or creates) TV show entries in the database.  Uses the application context (`with app.app_context():`) and *local* imports within the task function for database access.  It is designed to handle both new posts and edits to existing posts.
    *   **`reset_clicks`:** Resets the `clicks` count for all TV shows to 0.  Also uses the application context for database access.
    *   **`test_task`**: A simple task to verify Celery is working.
*   **`templates/`:**  Contains the Jinja2 templates for the web pages.
    *   **`base.html`:**  The base template that other templates inherit from.  Defines the overall layout.
    *   **`index.html`:**  The main page, displaying recently added shows, trending shows, and a search bar.
    *   **`show_details.html`:** Displays details for a specific TV show (fetched from the database), including the download link.  Handles incrementing the click count.
    *   **`shows.html`:** A page to list and filter all tv-shows
    *    **`404.html`:** Custom 404 error page
    *   **`500.html`:** Custom 500 error page.

*   **`__init__.py`:**  An empty file that makes the `tv_app` directory a Python package.
*   **`.env`:**  Stores sensitive configuration variables (database URL, Redis URL, API keys, etc.).  *Do not commit this file to version control.*
*   **`Procfile`:**  Specifies the commands to run the application on platforms like Heroku or Railway.  It defines the `web` process (using Gunicorn) and the `worker` process (Celery).
*   **`requirements.txt`:**  Lists the Python dependencies of the project (Flask, SQLAlchemy, Celery, etc.). Used by `pip` to install the required packages.
*   **`runtime.txt`**: Specifies the Python version to use (e.g., `python-3.12.2`).
*  ## Setup and Installation

1.  **Clone the repository:**
    ```bash
    git clone <your_repository_url>
    cd <your_repository_name>
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Linux/macOS
    venv\Scripts\activate  # On Windows
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Set environment variables:**
    Create a `.env` file in the project root and add the following (replace with your actual values):

    ```
    DATABASE_URL=postgresql://user:password@host:port/database  # Your PostgreSQL URL
    REDIS_URL=redis://:password@host:port/0  # Your Redis URL (including password if any)
    TMDB_BEARER_TOKEN=your_tmdb_bearer_token
    TELEGRAM_BOT_TOKEN=your_telegram_bot_token
    TELEGRAM_CHANNEL_ID=your_telegram_channel_id
    SECRET_KEY=your_flask_secret_key
    ```

5.  **Initialize the database:**
    ```bash
    flask db init  # Only needed the first time
    flask db migrate -m "Initial migration"
    flask db upgrade
    ```

6. **Enable the pg_trgm extension:**
  * **If Using models.py indexing:** No further action required. Migrate and upgrade will create the index.
   * **If Using direct SQL:**
      * Connect to your PostgreSQL database using `psql` or a GUI client.
      * Execute the following SQL command:
        ```sql
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        CREATE INDEX ix_tv_shows_show_name_trgm ON "tv_shows" USING gin (show_name gin_trgm_ops);
        ```

## Running the Application

### Locally

1.  **Start the Flask web server:**
    ```bash
    flask run
    ```
    2.  **Start the Celery worker (in a separate terminal):**
    ```bash
    celery -A tv_app.tasks worker -l info -c 1 -B
    ```
     * **`-A tv_app.tasks`:**  *Crucially* specifies the Celery application.
     * `-l info`: Sets logging level.
     * `-c 1`:  Limits concurrency to 1 worker process (important for avoiding database connection issues).
     * `-B`:  Starts Celery Beat (periodic tasks) alongside the worker.

### On Railway

1.  **Create a Railway project.**
2.  **Connect your GitHub repository.**
3.  **Add the necessary services:**
    *   PostgreSQL
    *   Redis
4.  **Set the environment variables** (as listed in the Setup section) in the Railway project settings. *Do not* commit your `.env` file.
5.  **Deploy.** Railway should automatically detect the `Procfile` and start the `web` and `worker` processes.  The `Procfile` should contain:
    ```
    web: gunicorn tv_app.app:app
    worker: celery -A tv_app.tasks worker -l info -c 1 -B
    ```

## Features

*   **TV Show Tracking:**  The application automatically fetches new posts from a specified Telegram channel, parses the posts to extract show information (name, season/episode, download link), and stores the data in a PostgreSQL database.
*   **TMDb Integration:**  If the Telegram post doesn't contain complete season/episode information, the application uses the TMDb API to fetch the latest season and episode details. It also retrieves poster images, overviews, and ratings from TMDb.
*   **Fuzzy Search:**  Uses the `pg_trgm` PostgreSQL extension for efficient fuzzy searching of show names.  Provides a fallback to `ilike` if no close matches are found.
*   **Click Tracking:**  Each time a user views the details of a TV show, a click counter is incremented.
*   **Trending Shows:**  Displays a list of the most-clicked shows on the homepage.
*   **Periodic Tasks (Celery Beat):**
    *   **`update_tv_shows`:**  Runs every 15 minutes to fetch new Telegram posts and update the database.
    *   **`reset_clicks`:**  Runs daily at midnight to reset the click counts for all shows.
*  **Show Filtering and Sorting:** Allows user to filter shows by genre, rating, year, and to order the list by name, date, or rating.
*  **Pagination:** The list of tv shows is paginated.
*  **Show Details Page:** Clicking a tv show displays all available informations of the show.

## Important Notes

*   **Circular Imports:** The project is structured to *avoid* circular imports.  Celery tasks import the Flask application (`app`) and database models (`db`, `TVShow`, `Genre`) *locally* within task functions, *inside* an application context (`with app.app_context():`).
*   **Concurrency:**  The Celery worker is configured with a concurrency of 1 (`-c 1`) to prevent database connection issues that can arise with multiple worker processes.
*   **`pg_trgm` Extension:**  Ensure the `pg_trgm` extension is enabled in your PostgreSQL database for fuzzy searching to work.
* **Telegram API:** You will need to create and configure a Telegram bot and use the provided token and chat id.
* **TMDb API:** To use TMDB you need to get the bearer token.

This comprehensive README provides a solid foundation for understanding, maintaining, and extending your TV Show Tracking application. It covers all the essential aspects, from project structure and setup to features and deployment.
