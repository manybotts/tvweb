# celeryconfig.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # Use the same Redis

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-6-hours': {
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute=0, hour='*/6'),  # Run every 6 hours
    },
}

broker_connection_retry_on_startup = True
