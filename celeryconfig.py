import os
from dotenv import load_dotenv
from celery.schedules import crontab

load_dotenv()

# Broker settings
broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Schedule
beat_schedule = {
    # 1. TV Shows (Telegram -> DB) - Every 15 mins
    'update-tv-shows-every-15-minutes': {
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': crontab(minute='*/15'),
    },
    # 2. Movies (Mongo -> DB) - Every 30 mins (NEW)
    'sync-movies-every-30-minutes': {
        'task': 'tv_app.tasks.sync_movies',
        'schedule': crontab(minute='*/30'),
    },
    # 3. Reset Clicks - Every 12 hours
    'reset-clicks-every-12-hours': {
        'task': 'tv_app.tasks.reset_clicks',
        'schedule': crontab(minute=0, hour='*/12'),
    },
}

broker_connection_retry_on_startup = True
timezone = 'UTC'
