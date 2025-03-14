# celeryconfig.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')  # Use the same Redis

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-1-minutes': {
        'task': 'tv_app.tasks.update_tv_shows',  # Full path to the task!
        'schedule': crontab(minute='*/15'),  # Run every 15 minutes
    },
}

# Optional: Other Celery settings (you can add these if needed)
# timezone = 'UTC'
# task_serializer = 'json'
# result_serializer = 'json'
# accept_content = ['json']
