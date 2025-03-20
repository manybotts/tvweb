# celeryconfig.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab  # Import crontab

load_dotenv()

# Celery configuration

# Broker URL (Redis) - where tasks are queued
broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Result backend (Redis) - where task results are stored
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Expire task results after 1 hour (3600 seconds)
result_expires = 3600

# Set Celery's timezone (IMPORTANT for scheduling)
timezone = 'UTC'

# Configure Celery Beat's schedule
beat_schedule = {
    'log-time-every-15-seconds': {  # Keep this for debugging
        'task': 'tv_app.tasks.log_current_time',
        'schedule': 15.0,  # Every 15 seconds
    },
    'update-tv-shows-every-minute': {  # Changed name for clarity
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute='*/1'),  # Run every minute
        #  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        #  CHANGE THIS FOR DIFFERENT SCHEDULES.
        #  Examples:
        #    - Every 5 minutes:  crontab(minute='*/5')
        #    - Every 10 minutes: crontab(minute='*/10')
        #    - Every hour at minute 0:  crontab(minute='0', hour='*')
        #    - Every day at 3:30 AM: crontab(minute='30', hour='3')
        #    - See Celery documentation for more crontab options:
        #      https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html#crontab-schedules
    },
}

# Retry connecting to the broker on startup (important for reliability)
broker_connection_retry_on_startup = True
