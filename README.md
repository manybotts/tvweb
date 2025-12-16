# iBOX TV (tvweb) — Deployment & Operations Guide

A compact, real‑world README that merges the latest deployment process with the important lessons from the older guide. It explains not just **what** to do, but **why**, so you avoid dependency roulette and broken servers at 3 a.m.

---

## 0) What you’re deploying

* **Stack**: Flask (Gunicorn) + Celery + Redis + TMDb + Telegram ingest
* **DB**: SQLite by default; Postgres (e.g., Supabase) recommended for production
* **Front**: Nginx reverse proxy
* **Admin**: `/nuke` protected by `ADMIN_TOKEN`, with cookie auth and lockout
* **SEO**: clean slugs, canonical tags, sitemap, robots, JSON‑LD on show pages

---

## 1) Requirements and versions (important)

**Python:** pinned to **3.8.x** for painless installs. That’s what the repo is tested on. Newer interpreters *may* work, but expect dependency whack‑a‑mole.

**System packages:** you need build tools and headers so pip can compile wheels that aren’t prebuilt for your OS/Python combo.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.8 python3.8-venv python3.8-dev \
    build-essential git libpq-dev nginx redis-server supervisor
```

> Why: `libpq-dev` is required for Postgres drivers, `build-essential` for C/C++ extensions. Installing these **before** `pip install -r requirements.txt` avoids the failure loop.

---

## 2) Clone, virtualenv, and dependencies

```bash
cd /root
git clone https://github.com/<you>/tvweb.git
cd /root/tvweb
python3.8 -m venv venv
./venv/bin/pip install -U pip wheel
./venv/bin/pip install -r requirements.txt
```

> The `requirements.txt` is tailored to Python 3.8 and this stack. Don’t freestyle versions unless you’re ready to debug transitive conflicts.

---

## 3) Environment configuration

Create `.env` in the project root:

```ini
# Flask
SECRET_KEY=change_this
FLASK_ENV=production

# Database (choose one)
# SQLite (quick start)
DATABASE_URL=sqlite:////root/tvweb/tv_shows.db
# Postgres (production recommended: e.g., Supabase session pooler)
# DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DBNAME

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

> Postgres on Supabase: Project Settings → Database → copy the **Session pooler** connection string.

---

## 4) Database create/reset

```bash
# Create tables (first run)
./venv/bin/python - <<'PY'
from tv_app.app import app
from tv_app.models import db
with app.app_context():
    db.create_all()
print("DB created")
PY
```

Drop and recreate (when schemas change):

```bash
./venv/bin/python - <<'PY'
from tv_app.app import app
from tv_app.models import db
with app.app_context():
    db.drop_all(); db.create_all()
print("DB reset complete")
PY
```

> Legacy backfill (old DB only): if you previously added a `tmdb_id` column and needed to populate it, run your backfill script after adding indices. Fresh installs don’t need this.

---

## 5) Supervisor (process manager)

We recommend **three programs**: Gunicorn web, Celery worker, Celery beat. (You can use a one‑process alternative with `-B`, see below.)

Create logs dir and configs:

```bash
sudo mkdir -p /var/log/ibox
```

**Gunicorn** → `/etc/supervisor/conf.d/ibox-gunicorn.conf`

```ini
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

**Celery worker** → `/etc/supervisor/conf.d/ibox-celery.conf`

```ini
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

**Celery beat** → `/etc/supervisor/conf.d/ibox-celerybeat.conf`

```ini
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

Apply and start:

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl restart ibox-gunicorn ibox-celery ibox-celerybeat
sudo supervisorctl status
```

**Alternate (single process with beat):** if you prefer the older style:

```ini
# /etc/supervisor/conf.d/ibox-celery-one.conf
[program:ibox-celery-one]
directory=/root/tvweb
command=/root/tvweb/venv/bin/celery -A tv_app.tasks worker -l INFO -c 1 -B
user=root
autostart=true
autorestart=true
stdout_logfile=/var/log/ibox/celery-one.out.log
stderr_logfile=/var/log/ibox/celery-one.err.log
stopasgroup=true
killasgroup=true
environment=PYTHONPATH="/root/tvweb"
```

Use either the 3‑process setup **or** the single `-B` program, not both.

---

## 6) Nginx (reverse proxy)

HTTP on 80 proxying to Gunicorn on **127.0.0.1:8001**. Serve static files and expose `/healthz`.

```nginx
# /etc/nginx/sites-available/ibox-tv.com
server {
    listen 80;
    server_name ibox-tv.com www.ibox-tv.com;

    client_max_body_size 32m;

    location /healthz {
        proxy_pass http://127.0.0.1:8001/healthz;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    location /static/ {
        alias /root/tvweb/tv_app/static/;
        access_log off;
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_read_timeout 300s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/ibox-tv.com /etc/nginx/sites-enabled/ibox-tv.com
sudo nginx -t && sudo systemctl reload nginx
```

**HTTPS:** Install Certbot and issue a certificate:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d ibox-tv.com -d www.ibox-tv.com
```

> Older docs that used port **8000** still work if you change Gunicorn `-b 127.0.0.1:8000` and update Nginx `proxy_pass` accordingly.

---

## 7) DNS

Create A records pointing your domain (and `www`) to the VPS IP. If using Cloudflare, proxied orange cloud is fine.

---

## 8) Operating the app (your preferred flow)

```bash
# edit a file
cd /root/tvweb/tv_app/templates
rm -f index.html
nano index.html

# restart web
sudo supervisorctl restart ibox-gunicorn

# trigger an ingest/update
curl -s -X POST https://ibox-tv.com/update

# logs
sudo supervisorctl tail -200 ibox-gunicorn stderr
sudo supervisorctl tail -200 ibox-celery stderr
sudo tail -200 /var/log/nginx/error.log
```

**Nuke panel**

* URL: `/nuke` → prompts for key `ADMIN_TOKEN`. On success sets a cookie for `NUKE_COOKIE_TTL_DAYS`.
* Lockout after repeated failures. To re‑enable:

```bash
redis-cli DEL nuke:enabled
sudo supervisorctl restart ibox-gunicorn
```

---

## 9) Search, SEO, and slugs

* Detail pages live at `/show/<slug>`; slugs are generated on insert and guaranteed unique.
* Homepage search uses Postgres trigram similarity when available, else `ILIKE` fallback.
* `sitemap.xml` lists detail pages and key lists; `/admin` intentionally 404s and is disallowed in `robots.txt`.

> If using Postgres: `CREATE EXTENSION IF NOT EXISTS pg_trgm;` for fast fuzzy search.

---

## 10) Why the dependency strategy works

* **Pin Python** (3.8.x) + **pin requirements** → reproducible installs.
* **Install system headers before pip** → builds succeed first try.
* **Use venv per project** → no global pollution.
* When upgrading, do it deliberately: bump one lib, test, commit the new lock.

---

## 11) Troubleshooting

* **Homepage 500, `no such column: tv_shows.clicks`**
  You changed models without migrating. Run the DB reset snippet, then restart services.

* **Celery won’t start**
  Run the exact Supervisor command manually to see the real traceback. Syntax errors in `tasks.py` are common.

* **`func.similarity` errors**
  You’re not on Postgres with `pg_trgm`. The app falls back to `ILIKE`, but you’ll lose the fuzzy ranking.

* **Nuke jumps to maintenance**
  Lockout tripped. Clear the Redis key shown above and restart Gunicorn.

---

## 12) Updating the app

```bash
cd /root/tvweb
git pull
./venv/bin/pip install -r requirements.txt
sudo supervisorctl restart ibox-gunicorn ibox-celery ibox-celerybeat
```

> Manual edits the old‑school way:

```bash
cd /root/tvweb/tv_app
rm -f app.py
nano app.py
sudo supervisorctl restart ibox-gunicorn
```

---

## License

You own your deployment. Ship responsibly.
# iBOX TV & Anime (Hybrid Platform)

A high-performance Flask streaming platform serving two distinct sites from a single codebase:
1. **TV Shows** (`ibox-tv.com`)
2. **Anime** (`anime.ibox-tv.com`)

## Features
* **Hybrid Isolation:** Content is strictly scoped by domain. Anime requests only see Anime content; TV requests only see TV content.
* **Smart Search:** Searches the current category first, but checks the other database for matches and prompts the user to switch if needed.
* **Dual-Channel Ingest:** Celery workers fetch content from two separate Telegram channels (one for TV, one for Anime).
* **PostgreSQL Search:** Utilizes `pg_trgm` for advanced fuzzy matching and performance.
* **Mobile Optimized:** Smart navigation bars that adapt to screen size.

---

## 1. Prerequisites
* Ubuntu VPS (20.04/22.04 recommended)
* Python 3.9+
* PostgreSQL
* Redis (for Celery task queue)
* Nginx (Web Server)
* Supervisor (Process Control)

---

## 2. Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url> /root/tvweb
    cd /root/tvweb
    ```

2.  **Set up Virtual Environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Environment Variables:**
    Create a `.env` file in the root directory:
    ```ini
    # --- Security ---
    SECRET_KEY=your_super_secret_key_here
    ADMIN_TOKEN=your_nuke_panel_password

    # --- Database (PostgreSQL) ---
    DATABASE_URL=postgresql://user:password@localhost/tv_shows_db

    # --- Telegram (Ingestion) ---
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
    # Channel 1: TV Shows
    TELEGRAM_CHANNEL_ID=-100xxxxxxxxxx
    # Channel 2: Anime
    TELEGRAM_ANIME_CHANNEL_ID=-100yyyyyyyyyy

    # --- TMDb API (Metadata) ---
    TMDB_BEARER_TOKEN=eyJhbGciOiJIUzI1NiJ9...

    # --- Redis (Celery Broker) ---
    REDIS_URL=redis://localhost:6379/0
    ```

---

## 3. Database Setup (Critical)

This project uses a custom schema to allow duplicate IDs across categories.

1.  **Create Database & User:**
    ```bash
    sudo -u postgres psql
    CREATE DATABASE tv_shows_db;
    CREATE USER myuser WITH PASSWORD 'mypassword';
    GRANT ALL PRIVILEGES ON DATABASE tv_shows_db TO myuser;
    \c tv_shows_db
    CREATE EXTENSION pg_trgm;  -- Required for fuzzy search
    \q
    ```

2.  **Initialize Tables:**
    ```bash
    cd /root/tvweb
    source venv/bin/activate
    flask shell
    >>> from tv_app.models import db
    >>> db.create_all()
    >>> exit()
    ```

3.  **Apply Hybrid Constraints:**
    Run this SQL command to ensure the database allows the same show ID in different categories (e.g., ID 123 in TV and ID 123 in Anime):
    ```bash
    sudo -u postgres psql -d tv_shows_db -c "DROP INDEX IF EXISTS ix_tv_shows_tmdb_id; CREATE UNIQUE INDEX IF NOT EXISTS ix_tmdb_category ON tv_shows (tmdb_id, category);"
    ```

---

## 4. Nginx Configuration

Create a config file at `/etc/nginx/sites-available/ibox-tv` with the following content. This handles both subdomains.

```nginx
# --- 1. Main TV Site (ibox-tv.com) ---
server {
    listen 80;
    server_name ibox-tv.com [www.ibox-tv.com](https://www.ibox-tv.com);

    location / {
        include proxy_params;
        proxy_pass [http://127.0.0.1:8000](http://127.0.0.1:8000);
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /static {
        alias /root/tvweb/tv_app/static;
        expires 30d;
    }
}

# --- 2. Anime Subdomain (anime.ibox-tv.com) ---
server {
    listen 80;
    server_name anime.ibox-tv.com;

    location / {
        include proxy_params;
        proxy_pass [http://127.0.0.1:8000](http://127.0.0.1:8000);
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /static {
        alias /root/tvweb/tv_app/static;
        expires 30d;
    }
}
After saving, run sudo ln -s /etc/nginx/sites-available/ibox-tv /etc/nginx/sites-enabled/ and sudo service nginx restart.

5. Supervisor Configuration
Use Supervisor to keep the app and Celery worker running. File: /etc/supervisor/conf.d/tvweb.conf

Ini, TOML

[program:tvweb]
directory=/root/tvweb
command=/root/tvweb/venv/bin/gunicorn -w 4 -b 127.0.0.1:8000 run:app
user=root
autostart=true
autorestart=true
environment=PATH="/root/tvweb/venv/bin"

[program:tvweb-celery]
directory=/root/tvweb
command=/root/tvweb/venv/bin/celery -A tv_app.tasks.celery worker --loglevel=info
user=root
autostart=true
autorestart=true
environment=PATH="/root/tvweb/venv/bin"
6. Maintenance Commands
Trigger an Update Manually:

Bash

curl -X POST http://localhost:8000/update
Reset/Wipe Database:

Bash

flask shell
>>> from tv_app.models import db, TVShow
>>> TVShow.query.delete()
>>> db.session.commit()
