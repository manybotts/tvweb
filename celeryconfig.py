# celeryconfig.py
from celery.schedules import crontab

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-5-minutes': {
        'task': 'tasks.update_tv_shows',
        'schedule': crontab(minute='*/15'),  # Run every 15 minutes
    },
}
