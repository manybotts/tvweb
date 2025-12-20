# iBOX TV (Hybrid Platform) — Deployment & Operations Guide

A compact, real‑world guide for deploying **iBOX TV**, a high-performance streaming platform serving three distinct content verticals from a single codebase:
1.  **TV Shows** (`ibox-tv.com`)
2.  **Anime** (`anime.ibox-tv.com`)
3.  **Movies** (`movies.ibox-tv.com`)

---

## 0) Architecture & Features

* **Stack:** Flask (Gunicorn) + Celery + Redis + SQLAlchemy (Postgres/SQLite).
* **Hybrid Isolation:** Requests are scoped by domain.
    * `ibox-tv.com` → Shows only **TV** content.
    * `anime.ibox-tv.com` → Shows only **Anime** content.
    * `movies.ibox-tv.com` → Shows only **Movie** content.
* **Smart Search:** A 3-way tabbed search interface that counts results across all categories and hints users if matches exist elsewhere.
* **Backfill Engine:** A "Nuke" admin panel (`/nuke`) capable of backfilling movies from external databases and managing duplicates.
* **SEO:** Clean slugs, canonical tags, sitemaps, and JSON-LD schema.

---

## 1) Requirements

**System:** Ubuntu 20.04/22.04 LTS.
**Python:** 3.8+ (Pinned to **3.8.x** recommended for stability with current dependencies).

**System Packages:**
You need headers for building Python wheels (especially for Postgres & C-extensions).

sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.8 python3.8-venv python3.8-dev \
    build-essential git libpq-dev nginx redis-server supervisor certbot python3-certbot-nginx

## 2) Installation
'''bash
cd /root
git clone [https://github.com/](https://github.com/)<your-username>/tvweb.git
cd /root/tvweb
#Create and activate virtual environment
python3.8 -m venv venv
./venv/bin/pip install -U pip wheel
./venv/bin/pip install -r requirements.txt
'''
## 3) Configuration (.env)
**Create a .env file in /root/tvweb/.env.**
'''bash
# --- Flask ---
SECRET_KEY=change_this_to_something_secure
FLASK_ENV=production

# --- Database ---
# SQLite (Testing):
# DATABASE_URL=sqlite:////root/tvweb/tv_shows.db
# Postgres (Production - Recommended):
DATABASE_URL=postgresql://USER:PASSWORD@localhost/tv_shows_db

# --- Redis ---
REDIS_URL=redis://localhost:6379/0

# --- Telegram Ingest ---
TELEGRAM_BOT_TOKEN=123456:YOUR_BOT_TOKEN
# Channel for TV Shows
TELEGRAM_CHANNEL_ID=-1001234567890
# Channel for Anime
TELEGRAM_ANIME_CHANNEL_ID=-1009876543210

# --- TMDb API ---
TMDB_BEARER_TOKEN=eyJhbGciOiJIUzI1NiJ9...

# --- Admin Panel (Nuke) ---
ADMIN_TOKEN=YourSecretAdminPassword
NUKE_COOKIE_TTL_DAYS=30
'''
## 4) Database Setup (Hybrid Constraints)
The system allows the same tmdb_id to exist if the categories are different (e.g., a Movie and a TV Show can share an ID, though rare). We need a custom unique index
   **1. Create Tables:**
      '''bash
         ./venv/bin/python - <<'PY'
from tv_app.app import app
from tv_app.models import db
with app.app_context():
    db.create_all()
print("DB created")
PY
      '''
   ***2. Apply Hybrid Constraint (Postgres Only): This allows duplicate TMDb IDs only if they are in different categories.***
      '''bash
         sudo -u postgres psql -d tv_shows_db -c "DROP INDEX IF EXISTS ix_tv_shows_tmdb_id; CREATE UNIQUE INDEX IF NOT EXISTS ix_tmdb_category ON tv_shows (tmdb_id, category);"
      '''
      3. Enable Fuzzy Search (Optional but Recommended):

Bash

sudo -u postgres psql -d tv_shows_db -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
5) Supervisor (Process Management)
We run Gunicorn (Web) and Celery (Background Tasks).

Config File: /etc/supervisor/conf.d/ibox-tv.conf

Ini, TOML

# 1. Gunicorn (Web Server)
[program:ibox-gunicorn]
directory=/root/tvweb
command=/root/tvweb/venv/bin/gunicorn -w 3 -b 127.0.0.1:8000 'tv_app.app:app'
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/ibox/gunicorn.out.log
stderr_logfile=/var/log/ibox/gunicorn.err.log
environment=PYTHONPATH="/root/tvweb"

# 2. Celery Worker (Task Processing)
[program:ibox-celery]
directory=/root/tvweb
command=/root/tvweb/venv/bin/celery -A tv_app.tasks worker --loglevel=INFO --concurrency=2
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/ibox/celery.out.log
stderr_logfile=/var/log/ibox/celery.err.log
environment=PYTHONPATH="/root/tvweb"

## 3. Celery Beat (Scheduled Tasks)
'''
[program:ibox-celerybeat]
directory=/root/tvweb
command=/root/tvweb/venv/bin/celery -A tv_app.tasks beat --loglevel=INFO
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/ibox/celerybeat.out.log
stderr_logfile=/var/log/ibox/celerybeat.err.log
environment=PYTHONPATH="/root/tvweb"
'''
***Apply Changes:***

'''Bash

sudo mkdir -p /var/log/ibox
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl restart all
'''
***6) Nginx (Master Configuration)***
This configuration handles TV, Anime, and Movies on separate subdomains, secured by a single Certbot certificate.

'''File: /etc/nginx/sites-available/one.ibox-tv.com '''

'''Nginx

# --- HTTP Redirects (Handled by Certbot usually) ---
server {
    listen 80;
    server_name ibox-tv.com [www.ibox-tv.com](https://www.ibox-tv.com) anime.ibox-tv.com movies.ibox-tv.com;
    return 301 https://$host$request_uri;
}

# --- Main HTTPS Server ---
server {
    listen 443 ssl http2;
    server_name ibox-tv.com [www.ibox-tv.com](https://www.ibox-tv.com) anime.ibox-tv.com movies.ibox-tv.com;

    # SSL Certificates (Managed by Certbot)
    ssl_certificate /etc/letsencrypt/live/[ibox-tv.com/fullchain.pem](https://ibox-tv.com/fullchain.pem);
    ssl_certificate_key /etc/letsencrypt/live/[ibox-tv.com/privkey.pem](https://ibox-tv.com/privkey.pem);
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Upstream
    set $app_upstream [http://127.0.0.1:8000](http://127.0.0.1:8000);

    # Security: Nuke Panel (No Proxy Interception)
    location /nuke {
        proxy_pass $app_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_intercept_errors off;
    }

    # Main App Proxy
    location / {
        proxy_pass $app_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Static Files (Caching)
    location /static {
        alias /root/tvweb/tv_app/static;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
    }
}
'''
***Enable & Secure:***

'''Bash

sudo ln -s /etc/nginx/sites-available/one.ibox-tv.com /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Expand SSL to cover new subdomains
sudo certbot --nginx --expand -d ibox-tv.com -d [www.ibox-tv.com](https://www.ibox-tv.com) -d anime.ibox-tv.com -d movies.ibox-tv.com
'''
7) Operations Manual
Admin Panel (Nuke)
Access: Visit /nuke and enter your ADMIN_TOKEN.

Backfill: Use the "Backfill" controls to fetch movie data.

Start: Begins the Celery task.

Pause: Signals the task to stop after the current batch.

Reset: Clears the Redis checkpoint to restart from scratch.

Purge: deletes all movie data (Use with caution!).

Common Commands
Bash

# Edit HTML templates
nano tv_app/templates/index.html

# Check Logs
sudo supervisorctl tail -f ibox-gunicorn stderr

# Restart Web App
sudo supervisorctl restart ibox-gunicorn

# Trigger Manual Ingest
curl -X POST [https://ibox-tv.com/update](https://ibox-tv.com/update)
Troubleshooting
"No such column: category": You didn't reset the DB after the hybrid update.

Search tabs showing 0: Ensure count_search_results in app.py is using ILike or pg_trgm correctly.

Nuke Lockout: Run redis-cli DEL nuke:enabled to reset access.
