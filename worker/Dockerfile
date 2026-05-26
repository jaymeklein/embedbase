FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# NOTE: sentence-transformers model pre-download added in Delivery 2
# RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

# Shell form required: exec-form JSON array cannot span multiple lines without \
# and does not expand shell variables like ${CELERY_CONCURRENCY:-2}
CMD celery -A worker.celery_app worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-2}
