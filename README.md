# iBOX TV (tvweb) — Deployment & Ops Guide

Flask + Celery + Redis pipeline that ingests TV posts from Telegram, enriches with TMDb, and serves SEO-friendly show pages with clean slugs. Deployed with Nginx + Supervisor. Yes, it actually works.

---

## Quick Start (Fresh VPS)

> Paths assume `/root/tvweb`. Adjust if you like suffering.

```bash
# 1) System deps
sudo apt update
sudo apt install -y python3-venv python3-dev build-essential nginx redis-server supervisor

# 2) App checkout and venv
cd /root
git clone https://github.com/<you>/tvweb.git
cd /root/tvweb
python3 -m venv venv
./venv/bin/pip install -U pip wheel
./venv/bin/pip install -r requirements.txt

# 3) Environment
nano .env
# paste the example below, save

# 4) Initialize the database
./venv/bin/python - <<'PY'
from tv_app.app import app
from tv_app.models import db
with app.app_context():
    db.create_all()
print("DB created.")
PY
```

### `.env` example

```ini
# Flask
SECRET_KEY=change_this
FLASK_ENV=production
DATABASE_URL=sqlite:////root/tvweb/tv_shows.db
# or Postgres:
# DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/tvweb

# Redis
REDIS_URL=redis://localhost:6379/0

# Telegram ingest
TELEGRAM_BOT_TOKEN=123456:abc_your_bot_token
TELEGRAM_CHANNEL_ID=-1001234567890

# TMDb
TMDB_BEARER_TOKEN=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Nuke admin
ADMIN_TOKEN=W@ngari327
NUKE_COOKIE_TTL_DAYS=30
```

---

## Supervisor

Three services: Gunicorn (Flask), Celery worker, Celery beat.

```ini
# /etc/supervisor/conf.d/ibox-gunicorn.conf
[program:ibox-gunicorn]
directory=/root/tvweb
command=/root/tvweb/venv/bin/gunicorn -w 3 -b 127.0.0.1:8001 'tv_app.app:app'
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/ibox/gunicorn.out.log
stderr_logfile=/var/log/ibox/gunicorn.err.log
stopsignal=TERM
stopasgroup=true
killasgroup=true
environment=PYTHONPATH="/root/tvweb"
```

```ini
# /etc/supervisor/conf.d/ibox-celery.conf
[program:ibox-celery]
directory=/root/tvweb
command=/root/tvweb/venv/bin/celery -A tv_app.tasks worker --loglevel=INFO --concurrency=2
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/ibox/celery.out.log
stderr_logfile=/var/log/ibox/celery.err.log
stopsignal=TERM
stopasgroup=true
killasgroup=true
environment=PYTHONPATH="/root/tvweb"
```

```ini
# /etc/supervisor/conf.d/ibox-celerybeat.conf
[program:ibox-celerybeat]
directory=/root/tvweb
command=/root/tvweb/venv/bin/celery -A tv_app.tasks beat --loglevel=INFO
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/ibox/celerybeat.out.log
stderr_logfile=/var/log/ibox/celerybeat.err.log
stopsignal=TERM
stopasgroup=true
killasgroup=true
environment=PYTHONPATH="/root/tvweb"
```

```bash
# Create log dir and start
sudo mkdir -p /var/log/ibox
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl restart ibox-gunicorn ibox-celery ibox-celerybeat
sudo supervisorctl status
```

---

## Nginx

Proxies to Gunicorn on `127.0.0.1:8001`, serves `/static/`, exposes `/healthz`.

```nginx
# /etc/nginx/sites-available/ibox-tv.com
server {
    listen 80;
    server_name ibox-tv.com www.ibox-tv.com;

    client_max_body_size 32m;
    keepalive_timeout 65;

    # Health
    location /healthz {
        proxy_pass http://127.0.0.1:8001/healthz;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    # Static
    location /static/ {
        alias /root/tvweb/tv_app/static/;
        access_log off;
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }

    # App
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_read_timeout 300s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    gzip on;
    gzip_types text/plain text/css application/json application/javascript application/xml image/svg+xml;
    gzip_min_length 1024;
    gzip_comp_level 5;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ibox-tv.com /etc/nginx/sites-enabled/ibox-tv.com
sudo nginx -t
sudo systemctl reload nginx
```

> Using TLS? Put a 301 HTTP→HTTPS server and a separate 443 block with your certs.

---

## App Features

* **Show pages**: `/show/<slug>` with canonical, social meta, JSON-LD.
* **Search**: homepage `?search=` uses trigram similarity on Postgres, falls back to `ILIKE` otherwise.
* **Listing**: `/shows` with filters (genre, rating bucket, year) + pagination.
* **Trending**: homepage “Most Watched Today” uses `TVShow.clicks`.
* **Sitemap/robots**: `/sitemap.xml`, robots disallow `/admin` and serve 404 on `/admin`.
* **ads.txt**: `/ads.txt` 301s to your Ezoic manager URL.
* **Health**: `/healthz` to check liveness.
* **Nuke panel**: `/nuke` guarded by `ADMIN_TOKEN`, cookie-based session, lockout after repeated failures.
* **Pipeline**: Telegram → Celery → TMDb → DB. Matching tolerates dotted acronyms, dropped articles, tiny stems, and uses year/season hints.

---

## Data Flow (Telegram → TMDb → DB)

1. **Telegram post**
   Line 1: title. Line 2: season/episode info. Link is taken from a `text_link` “CLICK HERE” entity or the last URL found.
2. **Normalizer & Scoring**

   * “A.T.O.M.” collapses to “ATOM” for matching.
   * Leading articles (“The”, “A”, “An”) are ignored.
   * Short stems allowed (e.g., `atom` vs `atomic`) when other signals align.
   * Year and season count are hints to break ties.
   * Small penalty when picking an all-caps acronym if your query was a normal word (to avoid “ATOM” stealing “Atomic”).
3. **De-dupe**
   If a row with the same `tmdb_id` exists, it’s replaced by the newest post.
4. **Slug**
   SEO-safe, unique, auto-generated on insert.

---

## Common Ops

### Your preferred edit flow

```bash
# Example: update a template
cd /root/tvweb/tv_app/templates
rm -f index.html
nano index.html
sudo supervisorctl restart ibox-gunicorn
```

### Reset database (drop and recreate tables)

```bash
cd /root/tvweb
./venv/bin/python - <<'PY'
from tv_app.app import app
from tv_app.models import db
with app.app_context():
    db.drop_all()
    db.create_all()
print("DB reset complete.")
PY
sudo supervisorctl restart ibox-gunicorn ibox-celery ibox-celerybeat
```

### Trigger an update

```bash
# via route (returns 202 Accepted)
curl -s -X POST https://ibox-tv.com/update
```

### Reprocess a specific Telegram post

```bash
# remove its dedupe flag in Redis, then trigger update
redis-cli DEL processed_messages:<message_id>
curl -s -X POST https://ibox-tv.com/update
```

### Logs

```bash
sudo supervisorctl status
sudo supervisorctl tail -200 ibox-gunicorn stderr
sudo supervisorctl tail -200 ibox-celery stderr
sudo tail -200 /var/log/nginx/error.log
sudo tail -200 /var/log/nginx/access.log
```

---

## Nuke Panel

* **URL**: `/nuke`
* **Auth**: prompts for key (`ADMIN_TOKEN`). On success sets a signed cookie for `NUKE_COOKIE_TTL_DAYS` (default 30).
* **Lockout**: two failed attempts disable the panel until manually re-enabled (clear Redis lock or restart).
* **Actions**: search, delete, and “group by identical download\_link” to purge duplicates fast.
* **Robots**: `noindex, nofollow` on all nuke pages.

> Clear lock example (key name may vary):

```bash
redis-cli DEL nuke:enabled
sudo supervisorctl restart ibox-gunicorn
```

---

## SEO

* Clean slugs and canonical tags on detail pages.
* Listing pages emit `prev/next` when applicable.
* `sitemap.xml` advertises all detail pages and recent lists.
* `/admin` answered with a dedicated 404 and disallowed in `robots.txt`.

---

## Postgres Trigram (optional)

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

Your `models.py` defines a GIN trigram index for `show_name` when Postgres is in use.

---

## Updating the App

```bash
cd /root/tvweb
git pull
./venv/bin/pip install -r requirements.txt
sudo supervisorctl restart ibox-gunicorn ibox-celery ibox-celerybeat
```

Manual edits (the caveman way you like):

```bash
cd /root/tvweb/tv_app
rm -f app.py
nano app.py
sudo supervisorctl restart ibox-gunicorn
```

---

## Troubleshooting

* **Homepage 500: `no such column: tv_shows.clicks`**
  You changed models but didn’t migrate. Drop/create tables, then restart.
* **Search fails on `func.similarity`**
  Not on Postgres or `pg_trgm` missing. Route falls back to `ILIKE`; check gunicorn stderr.
* **Template errors**
  Paste the whole file, not Frankenstein chunks. Restart gunicorn.
* **Nuke shows maintenance instantly**
  Lockout triggered. Clear Redis key, restart gunicorn, try again.

---

## License

You own your mess. Ship responsibly.
