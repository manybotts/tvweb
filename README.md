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

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.8 python3.8-venv python3.8-dev \
    build-essential git libpq-dev nginx redis-server supervisor certbot python3-certbot-nginx
