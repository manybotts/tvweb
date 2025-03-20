# tv_app/run_task.py
from tv_app.tasks import update_tv_shows
from tv_app.app import app  # Import the 'app' instance directly

if __name__ == "__main__":
    with app.app_context():
        update_tv_shows()
