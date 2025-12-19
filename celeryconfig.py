import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # Use the same Redis

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-5-minutes': {
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute='*/5'),
    },
    # --- NEW: Movie Sync Schedule (Debug Mode: Every 3 mins) ---
    'sync-movies-every-3-mins': {
        'task': 'tv_app.tasks.sync_movies',
        'schedule': crontab(minute='*/3'),
    },
    'reset-clicks-every-12-hours': {
        'task': 'tv_app.tasks.reset_clicks',
        'schedule': crontab(minute=0, hour='*/12'),  # ← every 12 hours at 00:00 and 12:00
    },
}
broker_connection_retry_on_startup = True
