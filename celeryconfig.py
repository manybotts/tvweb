# celeryconfig.py
from celery.schedules import crontab

print("CELERYCONFIG.PY IS BEING READ!")  # Keep this for now, to confirm

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-15-minutes': {
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute='*/1'),  # Every 15 minutes
    },
}

imports = ('tv_app.tasks',)
result_expires = 3600
timezone = 'UTC'  # Force UTC timezone
enable_utc = True
