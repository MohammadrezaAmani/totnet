# TotNet

TotNet is a Django application with Telegram bot processes and Celery workers. Docker Compose runs the web app, bot, worker, scheduler, PostgreSQL, and Redis services.

## Quick start

Requirements: Docker Engine with Docker Compose v2, and `make`.

```sh
make env       # create .env; existing .env is never overwritten
# Edit .env and set SECRET_KEY, DB_PASSWORD, and any provider/bot credentials.
make up        # build, migrate, then start the local development stack
```

Open <http://localhost:8000/admin/>. Create the first admin account with `make createsuperuser`. The development Compose override mounts the source tree and runs Django's development server. Use `make down` to stop services while keeping database and uploaded data.

The example settings are for local development. Before a public deployment, set a unique `SECRET_KEY`, `DEBUG=false`, the real `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, strong database credentials, and provider credentials. Configure TLS at a reverse proxy and review Django's proxy/HTTPS settings for that deployment. `make prod-up` omits the source bind mounts and starts Gunicorn.

## Services and persistence

- `web`: Django served by runserver in development, Gunicorn otherwise.
- `bot`: Telegram bot process.
- `worker` and `beat`: Celery task worker and scheduled-task runner.
- `db`: PostgreSQL 17 with a persistent volume.
- `redis`: persistent Celery broker/result backend, without an eviction memory limit.
- `cache`: disposable Redis cache, bounded by `REDIS_CACHE_MAXMEMORY` (512 MB by default) with LRU eviction.
- `adminer`: optional database UI, available with `make adminer-up` on localhost port 8081.

PostgreSQL, bot logs, media uploads, static files, and Celery Beat state live in named volumes. `make down` preserves them. `make down-v CONFIRM=YES` removes the volumes and permanently deletes that data.

## Make commands

Run `make` to list all targets. Common commands:

```sh
make build              # cached image build (BuildKit + uv download cache)
make build-no-cache     # force a clean image build
make up / make down     # start or stop local development services
make logs-web           # follow one service's logs
make migrate            # apply migrations
make check              # Django system checks
make test               # Django test suite
make lint               # Ruff linting on the host
make backup             # compressed PostgreSQL backup under backups/
make restore FILE=... CONFIRM=YES  # restore a backup; replaces database objects
```

Docker layers keep dependency installation separate from source changes. BuildKit retains the uv package download cache across builds, and `uv.lock` is installed in locked mode. Redis cache data can be safely cleared with `make cache-clear`; this does not touch Celery's Redis service.

To use Compose directly, `docker compose up --build` starts the development stack. `docker compose -f compose.yaml up --build -d` uses the image without the development bind mounts. The `.env` file is excluded from image builds and version control.
