SHELL := /bin/sh
.DEFAULT_GOAL := help
.ONESHELL:
.SHELLFLAGS := -eu -c

COMPOSE ?= docker compose
COMPOSE_PROD = $(COMPOSE) -f compose.yaml
UV ?= uv
FILE ?= backups/totnet-$(shell date +%Y%m%d-%H%M%S).dump

.PHONY: help env build build-no-cache up down down-v restart ps config logs logs-% \
	prod-up prod-down migrate makemigrations migration-check collectstatic \
	check test lint format shell dbshell createsuperuser cache-clear celery-ping \
	adminer-up adminer-down backup restore

help: ## Show available commands
	awk 'BEGIN {FS = ":.*##"}; /^[a-zA-Z0-9_.%/-]+:.*##/ {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

env: ## Create a local .env from the safe example (does not overwrite an existing file)
	if [ -e .env ]; then echo '.env already exists; leaving it unchanged'; else cp .env.example .env && chmod 600 .env; fi

build: ## Build the app image (BuildKit layer and uv download caches are enabled)
	DOCKER_BUILDKIT=1 $(COMPOSE) build

build-no-cache: ## Rebuild the app image without Docker layer cache
	DOCKER_BUILDKIT=1 $(COMPOSE) build --no-cache

up: ## Build, apply database migrations, and start the local development stack
	DOCKER_BUILDKIT=1 $(COMPOSE) build
	$(COMPOSE) run --rm migrate
	$(COMPOSE) up -d

down: ## Stop the local development stack (preserve named volumes)
	$(COMPOSE) down

down-v: ## Permanently delete stack volumes only with CONFIRM=YES
	if [ "$${CONFIRM:-}" != YES ]; then echo 'Refusing to remove data; rerun with CONFIRM=YES'; exit 2; fi
	$(COMPOSE) down --volumes --remove-orphans

restart: ## Restart local services
	$(COMPOSE) restart

ps: ## Show service status
	$(COMPOSE) ps

config: ## Validate and render Compose configuration
	$(COMPOSE) config --quiet

logs: ## Follow logs from all local services
	$(COMPOSE) logs -f --tail=200

logs-%: ## Follow logs for one service, e.g. make logs-web
	$(COMPOSE) logs -f --tail=200 $*

prod-up: ## Build, migrate, and start without development bind mounts
	DOCKER_BUILDKIT=1 $(COMPOSE_PROD) build
	$(COMPOSE_PROD) run --rm migrate
	$(COMPOSE_PROD) up -d

prod-down: ## Stop the stack without development overrides
	$(COMPOSE_PROD) down

migrate: ## Apply Django migrations
	$(COMPOSE) run --rm migrate

makemigrations: ## Create Django migrations in the running web container
	$(COMPOSE) exec web python manage.py makemigrations

migration-check: ## Check for model changes missing migrations
	$(COMPOSE) exec web python manage.py makemigrations --check --dry-run

collectstatic: ## Collect static files into the shared static volume
	$(COMPOSE) exec web python manage.py collectstatic --noinput

check: ## Run Django system checks
	$(COMPOSE) exec web python manage.py check

test: ## Run Django tests
	$(COMPOSE) exec web python manage.py test

lint: ## Run Ruff lint checks locally
	$(UV) run ruff check .

format: ## Format Python files locally with Ruff
	$(UV) run ruff format .

shell: ## Open Django shell in the web container
	$(COMPOSE) exec web python manage.py shell

dbshell: ## Open a PostgreSQL shell in the database container
	$(COMPOSE) exec db sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

createsuperuser: ## Create a Django admin user
	$(COMPOSE) exec web python manage.py createsuperuser

cache-clear: ## Clear only the disposable Redis cache database
	$(COMPOSE) exec cache redis-cli -n 0 FLUSHDB

celery-ping: ## Ping Celery workers
	$(COMPOSE) exec worker celery -A config inspect ping

adminer-up: ## Start optional Adminer database UI on localhost
	$(COMPOSE) --profile dbadmin up -d adminer

adminer-down: ## Stop optional Adminer database UI
	$(COMPOSE) stop adminer

backup: ## Create a compressed PostgreSQL backup (override with FILE=path.dump)
	mkdir -p "$(dir $(FILE))"
	$(COMPOSE) exec -T db sh -c 'pg_dump -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -Fc' > "$(FILE)"
	echo "Backup written to $(FILE)"

restore: ## Restore FILE=backup.dump only after explicitly setting CONFIRM=YES
	if [ "$${CONFIRM:-}" != YES ]; then echo 'Refusing restore: rerun with CONFIRM=YES FILE=backup.dump'; exit 2; fi
	if [ ! -s "$(FILE)" ]; then echo "Backup file missing or empty: $(FILE)"; exit 2; fi
	cat "$(FILE)" | $(COMPOSE) exec -T db sh -c 'pg_restore --clean --if-exists --no-owner -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
