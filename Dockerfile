FROM docker-remote-docker-io.art.lmru.tech/python:3.10-slim-bullseye AS builder

LABEL maintainer="stas"
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV POETRY_HTTP_TIMEOUT=30
ENV POETRY_HTTP_RETRIES=2

COPY pyproject.toml poetry.lock /app/

RUN pip install --no-cache-dir poetry \
    && poetry config virtualenvs.create false \
    && poetry source add --priority=primary art https://art.lmru.tech/artifactory/api/pypi/python-remote-pypi/simple \
    && poetry source add --priority=supplemental dostovernost https://art.lmru.tech/artifactory/api/pypi/pypi-local-dostovernost/simple \
    && poetry lock --no-interaction --no-ansi \
    && poetry install --no-interaction --no-ansi --no-root

COPY . /app

FROM docker-remote-docker-io.art.lmru.tech/python:3.10-slim-bullseye

WORKDIR /app

COPY --from=builder /usr/local /usr/local
COPY --from=builder /app /app

ENV PYTHONPATH=/app
ENV POETRY_CACHE_DIR=/app/.cache/pypoetry

USER 101

EXPOSE 8000

CMD ["python3", "run.py"]
