FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repo root (see docker-compose / CI) so the worker image
# carries both the worker package and the shared `api` package it imports.
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default embedding model at build time (the worker runs the
# embedding step). Override EMBEDDING_MODEL at runtime to use a different one.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY api ./api
COPY worker ./worker

# Shell form required: exec-form JSON array cannot span multiple lines without \
# and does not expand shell variables like ${CELERY_CONCURRENCY:-2}
CMD celery -A worker.celery_app worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-2}
