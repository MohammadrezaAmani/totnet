# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.14-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.21

FROM ${UV_IMAGE} AS uv-bin

FROM ${PYTHON_IMAGE} AS dependencies
COPY --from=uv-bin /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_CACHE_DIR=/root/.cache/uv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependency installation only depends on the lock and project metadata, so source
# edits do not invalidate this layer. BuildKit also keeps uv's download cache.
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-dev --no-install-project --no-editable

FROM ${PYTHON_IMAGE} AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
    && mkdir -p /app /data/media /data/static /data/logs /data/celerybeat \
    && chown -R app:app /app /data

COPY --from=dependencies --chown=app:app /opt/venv /opt/venv
COPY --chown=app:app . /app

WORKDIR /app
USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "90", "--access-logfile", "-", "--error-logfile", "-"]
