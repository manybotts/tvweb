# celeryconfig.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # Use the same Redis

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-10-minutes': {  # Keep name consistent
        'task': 'tasks.update_tv_shows', # CORRECTED task path
        'schedule': crontab(minute='*/10'),  # Run every 10 minutes
    },
     'reset-clicks-daily': {  # <--- ADD THIS for the new task
        'task': 'tasks.reset_clicks',  # <--- CORRECT PATH to the new task
        'schedule': crontab(hour=0, minute=0),   # Run at midnight UTC
    },
}

broker_connection_retry_on_startup = True
timezone = 'UTC' # add this
