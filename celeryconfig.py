# celeryconfig.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # Use the same Redis

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-15-minutes': {  # Corrected schedule name
        'task': 'tv_app.tasks.update_tv_shows',  # Full path to the task!
        'schedule': crontab(minute='*/15'),  # Run every 15 minutes
    },
}

broker_connection_retry_on_startup = True  # Add this line

# Add these lines for more verbose logging (for debugging):
worker_redirect_stdouts = True
worker_redirect_stdouts_level = 'DEBUG'
