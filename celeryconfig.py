# celeryconfig.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # Use the same Redis

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-15-minutes': {  # Changed the name for clarity
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute='*/10'),  # Run every 15 minutes.  CORRECTED.
    },
}

broker_connection_retry_on_startup = True
