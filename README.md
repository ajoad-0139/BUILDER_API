# Builder API

A REST API for a multi-tenant website builder. Users register, pick a **theme**, create **sites** from it, add **pages**, attach **images and videos**, and then **preview** or **publish** the result as static JSON and asset bundles ready to be served from a CDN.

Built with **Django 6**, **Django REST Framework**, **PostgreSQL** and **Redis**, with JWT authentication, a Redis-backed response cache and a per-user token-bucket rate limiter.

![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Django](https://img.shields.io/badge/django-6.1-092E20)
![DRF](https://img.shields.io/badge/DRF-3.18-red)
![PostgreSQL](https://img.shields.io/badge/postgresql-16-336791)
![Redis](https://img.shields.io/badge/redis-7-DC382D)
![Tests](https://img.shields.io/badge/tests-pytest-0A9EDC)

---

## Table of contents

- [Features](#features)
- [Tech stack](#tech-stack)
- [Quick start](#quick-start)
- [`run.sh` reference](#runsh-reference)
- [Manual setup](#manual-setup)
- [Configuration](#configuration)
- [API overview](#api-overview)
- [Usage examples](#usage-examples)
- [Rate limiting](#rate-limiting)
- [Caching](#caching)
- [Preview and publish](#preview-and-publish)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Production checklist](#production-checklist)
- [Troubleshooting](#troubleshooting)

---

## Features

- **JWT authentication**: register, login, refresh, logout and profile endpoints. Refresh tokens rotate and are blacklisted after use.
- **Themes**: reusable starting points made of header, footer, body, CSS and optional JS files, plus a thumbnail and their own image and video libraries.
- **Sites**: created from an active theme, with language (`en`, `bn`), status (`draft`, `published`, `archived`), custom domain, favicon and SEO / Open Graph metadata.
- **Pages**: per-site pages with their own HTML, CSS, optional JS, slug, canonical URL and SEO fields.
- **Media**: multi-file image and video upload for themes and sites, with extension and size validation.
- **Preview and publish**: export a site into per-page JSON documents and copied assets, written atomically, with stale files cleaned up.
- **Ownership rules**: owners and staff can modify a resource; everyone else is denied.
- **Redis response cache**: versioned namespaces and automatic invalidation on writes. It degrades to the database if Redis fails.
- **Rate limiting**: per-user token bucket implemented as one atomic Redis Lua script, with `RateLimit` response headers.
- **Consistent responses**: every endpoint returns the same `success / message / data / errors` envelope.
- **Test suite**: pytest, with fixtures, covering auth, themes, sites, pages, media, cache and rate limiting.

## Tech stack

| Layer | Technology |
| --- | --- |
| Language | Python 3.12+ |
| Framework | Django 6.1, Django REST Framework 3.18 |
| Auth | `djangorestframework-simplejwt` (with token blacklist) |
| Database | PostgreSQL (`psycopg` 3) |
| Cache / rate limit | Redis 7 (`django-redis`, `redis-py`, Lua script) |
| Media | Pillow, local filesystem storage (S3 backend configured) |
| Testing | pytest, pytest-django, pytest-cov |
| Local infra | Docker Compose |

---

## Quick start

**Prerequisites:** Python 3.12+, Docker (with Compose), and a Bash shell (Linux, macOS, WSL or Git Bash).

```bash
git clone <your-repo-url> BUILDER_API
cd BUILDER_API

chmod +x run.sh
./run.sh
```

That single command:

1. creates `.env` from `.env.example` if it does not exist,
2. creates a `.venv` and installs `requirements.txt`,
3. starts **PostgreSQL** and **Redis** in Docker and waits until both are healthy,
4. applies migrations,
5. starts the Django server at <http://127.0.0.1:8000>.

To also run the test suite with coverage in a separate terminal window once the app is up:

```bash
./run.sh --test
```

Stop the containers when you are done:

```bash
./run.sh --stop
```

## `run.sh` reference

```text
Usage: ./run.sh [options]

  -t, --test         Run tests WITH COVERAGE in a separate terminal once the app is up
                     (falls back to the background of the current terminal)
  -p, --port PORT    Django dev server port (default: 8000)
      --no-install   Skip pip install for faster restarts
      --superuser    Interactively create a Django superuser before starting
      --down         Stop the Docker containers when the app exits
      --stop         Stop the Docker containers and exit
  -h, --help         Show help
```

| Command | What it does |
| --- | --- |
| `./run.sh` | Database + Redis + migrations + app |
| `./run.sh -t` | Same, plus tests with coverage in a new terminal |
| `./run.sh -t -p 9000 --down` | Custom port, tests, containers stopped on exit |
| `./run.sh --superuser` | Create an admin user first, then start |
| `./run.sh --stop` | Stop PostgreSQL and Redis |

Database and Redis data live in named Docker volumes (`postgres_data`, `redis_data`), so they survive restarts. To wipe them: `docker compose down -v`.

---

## Manual setup

If you prefer to run each step yourself:

```bash
# 1. Environment file
cp .env.example .env

# 2. Virtual environment and dependencies
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. PostgreSQL and Redis
docker compose up -d db redis

# 4. Database schema
python manage.py migrate

# 5. (Optional) admin user for /admin/
python manage.py createsuperuser

# 6. Run
python manage.py runserver
```

---

## Configuration

Settings are read from `.env` using `django-environ`. Copy `.env.example` and adjust.

| Variable | Default | Description |
| --- | --- | --- |
| `DB_NAME` | required | PostgreSQL database name (also used by the Docker container) |
| `DB_USER` | required | PostgreSQL user |
| `DB_PASSWORD` | required | PostgreSQL password |
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `REDIS_URL` | `redis://127.0.0.1:6379/1` | Redis database for the response cache |
| `RATE_LIMIT_REDIS_URL` | `redis://127.0.0.1:6379/2` | Redis database for the rate limiter |
| `RATE_LIMIT_ENABLED` | `True` | Turn rate limiting on or off |
| `RATE_LIMIT_SOCKET_TIMEOUT` | `0.08` | Redis timeout (seconds) for the limiter |
| `API_CACHE_ENABLED` | `True` | Turn the response cache on or off |
| `API_CACHE_TTL` | `300` | Cache lifetime in seconds |
| `API_CACHE_INVALIDATE_ON_COMMIT` | `True` | Invalidate cache only after the DB transaction commits |
| `REDIS_KEY_PREFIX` | `presto-cache` | Prefix for cache keys |
| `REDIS_CONNECT_TIMEOUT` / `REDIS_SOCKET_TIMEOUT` | `2` | Redis timeouts for the cache |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `AWS_S3_REGION_NAME` | required | S3 settings. `settings.py` reads them without defaults, so they must be present even if S3 is unused. Dummy values work locally. |

Other values live directly in `config/settings.py`: JWT lifetimes, upload pool size, export root and CDN base URL (`EXPORT_*`), and the rate-limit policies.

---

## API overview

**Base URL:** `http://127.0.0.1:8000/api/v1/`

Send the access token on every protected request:

```http
Authorization: Bearer <access_token>
```

Access tokens last **15 minutes**; refresh tokens last **7 days** and rotate on use.

### Auth

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| POST | `auth/register` | none | Create an account and receive tokens |
| POST | `auth/login` | none | Log in and receive tokens |
| POST | `auth/refresh` | none | Exchange a refresh token for new tokens |
| POST | `auth/logout` | none | Blacklist a refresh token |
| GET | `auth/me` | required | Current user profile |

### Themes

| Method | Endpoint | Description |
| --- | --- | --- |
| GET, POST | `themes` | List / create themes |
| GET, PATCH, DELETE | `themes/{theme_id}` | Retrieve / update / delete a theme |
| GET, POST | `themes/{theme_id}/images` | List / upload theme images |
| GET, PATCH, DELETE | `themes/{theme_id}/images/{image_id}` | Manage one image |
| GET, POST | `themes/{theme_id}/videos` | List / upload theme videos |
| GET, PATCH, DELETE | `themes/{theme_id}/videos/{video_id}` | Manage one video |

### Sites

| Method | Endpoint | Description |
| --- | --- | --- |
| GET, POST | `sites` | List (filter by `status`, `language`, `search`) / create sites |
| GET, PATCH, DELETE | `sites/{site_id}` | Retrieve / update / delete a site |
| GET, POST | `sites/{site_id}/images` | List / upload site images |
| GET, PATCH, DELETE | `sites/{site_id}/images/{image_id}` | Manage one image |
| GET, POST | `sites/{site_id}/videos` | List / upload site videos |
| GET, PATCH, DELETE | `sites/{site_id}/videos/{video_id}` | Manage one video |

### Pages

| Method | Endpoint | Description |
| --- | --- | --- |
| GET, POST | `sites/{site_id}/pages` | List / create pages |
| GET, PATCH, DELETE | `sites/{site_id}/pages/{page_id}` | Retrieve / update / delete a page |

### Export

| Method | Endpoint | Description |
| --- | --- | --- |
| POST | `sites/{site_id}/preview` | Export the site in preview mode |
| POST | `sites/{site_id}/publish` | Export the site in publish mode |

### Response format

All endpoints use the same envelope:

```json
{
  "success": true,
  "message": "Sites retrieved",
  "data": { "...": "..." },
  "errors": null
}
```

Errors set `success` to `false`, `data` to `null` and fill `errors`. List endpoints use limit/offset pagination (`?limit=10&offset=0`, default limit 10, maximum 100) and return:

```json
{
  "items": [],
  "pagination": { "count": 0, "limit": 10, "offset": 0, "next": null, "previous": null }
}
```

### Upload limits

| Asset | Allowed extensions | Max size |
| --- | --- | --- |
| HTML | `html`, `htm` | 500 KB |
| CSS / JS | `css` / `js` | 1 MB each |
| Images | `jpg`, `jpeg`, `png`, `gif`, `webp`, `svg` | 5 MB |
| Thumbnails, favicon, OG image | image types (and `ico` for favicons) | 2 MB |
| Videos | `mp4`, `webm`, `mov`, `m4v`, `ogv` | 200 MB |

---

## Usage examples

```bash
API=http://127.0.0.1:8000/api/v1

# Register (returns access + refresh tokens)
curl -X POST $API/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "email": "alice@example.com", "password": "S3cure-pass-123"}'

# Log in and capture the access token
TOKEN=$(curl -s -X POST $API/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "S3cure-pass-123"}' \
  | python -c "import sys, json; print(json.load(sys.stdin)['data']['access'])")

# Who am I?
curl $API/auth/me -H "Authorization: Bearer $TOKEN"

# Create a theme (multipart upload)
curl -X POST $API/themes \
  -H "Authorization: Bearer $TOKEN" \
  -F name="Modern Business" -F language=en \
  -F thumbnail=@thumb.png \
  -F header=@header.html -F footer=@footer.html -F body=@body.html \
  -F css_file=@index.css

# Create a site from that theme (use the theme id from the previous response)
curl -X POST $API/sites \
  -H "Authorization: Bearer $TOKEN" \
  -F name="My Site" -F language=en -F theme=<theme_id>

# Add a page
curl -X POST $API/sites/<site_id>/pages \
  -H "Authorization: Bearer $TOKEN" \
  -F title="Home" -F page_type=home \
  -F html_file=@home.html -F css_file=@home.css

# List draft sites
curl "$API/sites?status=draft&limit=20" -H "Authorization: Bearer $TOKEN"

# Preview, then publish
curl -X POST $API/sites/<site_id>/preview -H "Authorization: Bearer $TOKEN"
curl -X POST $API/sites/<site_id>/publish -H "Authorization: Bearer $TOKEN"
```

The Django admin is available at <http://127.0.0.1:8000/admin/> after you create a superuser.

---

## Rate limiting

Authenticated requests pass through a per-user **token bucket** stored in Redis (one atomic Lua script, `sites/ratelimit/token_bucket.lua`).

| Policy | Applies to | Capacity | Refill rate |
| --- | --- | --- | --- |
| `read` | `GET`, `HEAD` | 120 | 2 tokens/s |
| `write` | other non-multipart requests | 30 | 0.5 tokens/s |
| `upload` | `multipart/*` requests | 250 | 2 tokens/s (cost grows with upload size) |

- Exceeding a bucket returns **HTTP 429** with `RateLimit` headers and a retry hint.
- Multipart uploads must send `Content-Length` (**411** otherwise) and may not exceed 100 MB (**413**).
- `OPTIONS` requests, `/admin/` and anonymous requests are not limited.
- If Redis is unreachable the limiter **fails open**, so the API keeps working.

Disable it locally with `RATE_LIMIT_ENABLED=False`.

## Caching

GET responses for themes, sites, pages and media are cached in Redis as serialized data (never model instances). Each namespace (for example `user:{id}:sites`) carries a version number in its keys; a write bumps the version so older entries become unreachable. By default invalidation runs after the transaction commits. If Redis is down, requests fall back to the database.

Disable it with `API_CACHE_ENABLED=False`.

## Preview and publish

`POST /sites/{id}/preview` and `/publish` write a static bundle under `EXPORT_ROOT` (default `./exports`):

```text
exports/
└── beta/                      # EXPORT_VERSION
    ├── preview/<site-slug>/
    └── publish/<site-slug>/
        ├── header/footer JSON  # shared site metadata
        ├── <page-slug>.json    # one JSON document per page
        └── media files         # images, videos, CSS, JS
```

Files are written atomically, files that no longer belong to the site are removed, and URLs inside the JSON point to `EXPORT_CDN_BASE_URL`. Only the site owner or staff can export. A response looks like:

```json
{ "mode": "publish", "path": "beta/publish/my-site", "pages": ["about", "home"] }
```

---

## Testing

The easiest way is `./run.sh --test`, which runs everything in a second terminal while the app is live. To run tests yourself (PostgreSQL and Redis must be running):

```bash
source .venv/bin/activate

# All tests
pytest

# With coverage (terminal + HTML report in htmlcov/)
pytest --cov=sites --cov=config --cov-report=term-missing --cov-report=html

# One file or one test
pytest sites/tests/test_auth.py
pytest sites/tests/test_pages.py -k "create"
```

Notes:

- pytest-django creates and destroys a separate `test_<DB_NAME>` database, so your development data is untouched. The database user must be allowed to create databases (the Docker `db` user is).
- Uploaded files go to a temporary directory per test, never into `media/`.
- Rate-limit tests need Redis and are skipped automatically when it is not reachable. They use Redis database 15 (override with `RATE_LIMIT_TEST_REDIS_URL`).
- Open `htmlcov/index.html` in a browser for the coverage report.

| Test module | Covers |
| --- | --- |
| `test_auth.py` | Register, login, refresh, logout, profile |
| `test_themes.py`, `test_theme_media.py` | Theme CRUD, theme images and videos |
| `test_sites.py`, `test_site_media.py` | Site CRUD, site images and videos |
| `test_pages.py` | Page CRUD, slugs, validation |
| `test_cache.py` | Cache hits, invalidation, fallbacks |
| `test_ratelimit.py` | Token bucket, headers, fail-open behaviour |

---

## Project structure

```text
BUILDER_API/
├── run.sh                    # One-command launcher (DB, Redis, app, optional tests)
├── docker-compose.yml        # PostgreSQL + Redis
├── .env.example              # Environment template
├── manage.py
├── pytest.ini
├── requirements.txt
├── config/                   # Project settings, root URLs, ASGI/WSGI
└── sites/                    # The main app
    ├── models.py             # Theme, Site, Page, Image, Video
    ├── urls.py               # /api/v1/ routes
    ├── views/                # auth, theme, site, page, export
    ├── serializers/          # Input / query / output serializers
    ├── services/             # Business logic (site, theme, page, export)
    ├── cache/                # Cache core and invalidation signals
    ├── ratelimit/            # Middleware, engine, Lua token bucket
    ├── utils/                # Pagination, responses, storage, constraints
    ├── validators.py         # File extension and size validators
    ├── exceptions.py         # API errors and exception handler
    ├── admin.py              # Django admin configuration
    ├── migrations/
    └── tests/                # pytest suite
```

The code follows a layered design: **views** handle HTTP and permissions, **serializers** validate input and shape output, and **services** hold the business rules and database work.

---

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `ImproperlyConfigured: Set the DB_NAME environment variable` | `.env` is missing or incomplete. Copy `.env.example` to `.env`. |
| `ImproperlyConfigured` mentioning `AWS_...` | Keep the dummy `AWS_*` values from `.env.example` in `.env`. |
| `Docker daemon is not running` | Start Docker Desktop or the Docker service, then re-run. |
| `port is already allocated` (5432 or 6379) | Stop the local Postgres/Redis, or change `DB_PORT` in `.env` (the Redis port is fixed at 6379 in `docker-compose.yml`). |
| Database auth errors after changing `DB_USER` / `DB_PASSWORD` | The Postgres volume keeps the old credentials. Reset with `docker compose down -v`. |
| Rate-limit tests are skipped | Redis is not reachable on `127.0.0.1:6379`. Start it with `docker compose up -d redis`. |
| Requests suddenly return `429` | You hit the rate limit. Wait for the bucket to refill or set `RATE_LIMIT_ENABLED=False` locally. |
| `Python 3.12+ is required` | Install a newer Python; Django 6.x does not support older versions. |
| Tests did not open a new terminal | No supported terminal emulator was found, so they ran in the current terminal. This is expected on headless machines. |

---

