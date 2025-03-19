# celeryconfig.py
import os
from dotenv import load_dotenv

load_dotenv()

broker_url = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
result_backend = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

result_expires = 3600  # Expire results after 1 hour (adjust as needed)

# Configure Celery Beat's schedule
beat_schedule = {
    'update-tv-shows-every-5-minutes': {  # Descriptive name
        'task': 'tv_app.tasks.update_tv_shows',
        'schedule': 300.0,  # Run every 5 minutes (300 seconds)
    },
}

broker_connection_retry_on_startup = True
