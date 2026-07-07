FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repo root (see docker-compose / CI) so the worker image
# carries both the worker package and the shared `api` package it imports.

# Core worker deps (lightweight — no torch, no chromadb). Kept in its own layer
# so adding a small dependency here does not rebuild the heavy ML layer below.
COPY worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ML / ingestion adapter deps (sentence-transformers → torch, chromadb, parsers,
# vector-store drivers). Pass --build-arg INSTALL_ML=false to produce a slim
# image for CI smoke tests (skips the multi-minute torch install).
ARG INSTALL_ML=true
COPY worker/requirements-ml.txt .
RUN if [ "$INSTALL_ML" = "true" ]; then \
        pip install --no-cache-dir -r requirements-ml.txt; \
    fi

# Native runtime libs for docling's OpenCV dependency (cv2): libGL.so.1 and
# libgthread/glib. python:3.12-slim omits them, so without this docling fails at
# import with "libGL.so.1: cannot open shared object file" on .docx/.pptx or the
# docling PDF backend. Gated on INSTALL_ML so the slim CI image stays slim.
RUN if [ "$INSTALL_ML" = "true" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            libgl1 libglib2.0-0 \
        && rm -rf /var/lib/apt/lists/*; \
    fi

# Pre-download the default embedding model at build time (the worker runs the
# embedding step). Skipped when INSTALL_ML=false (the model would be unavailable
# anyway). Override EMBEDDING_MODEL at runtime to use a different one (rebuild required).
RUN if [ "$INSTALL_ML" = "true" ]; then \
        python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"; \
    fi
# Optionally pre-download the docling layout/OCR models at build time so the
# first ingest doesn't pay the ~500 MB download. Default false (lazy download on
# first use — smaller image, ~3s extra on first scanned-PDF ingest).
ARG EMBEDBASE_DOCLING_MODELS=false
RUN if [ "$INSTALL_ML" = "true" ] && [ "$EMBEDBASE_DOCLING_MODELS" = "true" ]; then \
        python -c "from docling.document_converter import DocumentConverter; DocumentConverter()"; \
    fi

# Prevent any HuggingFace Hub network calls at runtime; rely solely on the cache above.
ENV HF_HUB_OFFLINE=1

# The worker imports the shared `api` package during ingestion (status updates via
# api.services.documents, object storage, MCP), which pulls in api's web/service
# dependencies. Install them so a freshly built worker can finish an ingest —
# previously only worker/*.txt were installed here, so the ingest status-update step
# failed on missing fastapi/bcrypt/boto3/etc. Placed after the ML layer so that
# expensive cache is preserved.
COPY api/requirements.txt ./api-requirements.txt
RUN pip install --no-cache-dir -r ./api-requirements.txt

COPY api ./api
COPY worker ./worker

# Shell form required: exec-form JSON array cannot span multiple lines without \
# and does not expand shell variables like ${CELERY_CONCURRENCY:-2}
CMD celery -A worker.celery_app worker --loglevel=info --concurrency=${CELERY_CONCURRENCY:-2}
