# celeryconfig.py
from celery.schedules import crontab

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-15-minutes': {  # A unique name for this task
        'task': 'tv_app.tasks.update_tv_shows',     # The FULL PATH to your task
        'schedule': crontab(minute='*/15'), # Run every 15 minutes
        # 'args': (arg1, arg2),  # Optional arguments to the task
    },
}

# VERY IMPORTANT: Tell Celery to use this schedule
imports = ('tv_app.tasks',)  # Import your tasks module
result_expires = 3600  # Optional: Expire results after 1 hour
