# celeryconfig.py
import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

beat_schedule = {
    'update-tv-shows-every-10-minutes': {
        'task': 'tv_app.tasks.update_tv_shows',  # CORRECT
        'schedule': crontab(minute='*/10'),
    },
    'reset-clicks-daily': {
        'task': 'tv_app.tasks.reset_clicks',  # CORRECT
        'schedule': crontab(hour=0, minute=0),
    },
}

broker_connection_retry_on_startup = True
timezone = 'UTC'
