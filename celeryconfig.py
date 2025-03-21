# celeryconfig.py - UPDATED for reset_clicks and correct task paths
import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # Use the same Redis

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-15-minutes': {
        'task': 'tv_app.tasks.update_tv_shows',  # Correct task path
        'schedule': crontab(minute='*/1'),  # Run every 15 minutes
    },
    'reset-clicks-daily': {  # NEW TASK
        'task': 'tv_app.tasks.reset_clicks',  # Correct task path
        'schedule': crontab(hour=0, minute=0),  # Run daily at midnight
    },
}
broker_connection_retry_on_startup = True
