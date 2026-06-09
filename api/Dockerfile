FROM python:3.12-slim

WORKDIR /app

# System deps for PyMuPDF, tree-sitter grammars, and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Core API deps (lightweight — no torch, no chromadb)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ML / ingestion adapter deps (sentence-transformers → torch, chromadb, etc.)
# Pass --build-arg INSTALL_ML=false to produce a slim image for CI smoke tests.
ARG INSTALL_ML=true
COPY requirements-ml.txt .
RUN if [ "$INSTALL_ML" = "true" ]; then \
        pip install --no-cache-dir -r requirements-ml.txt; \
    fi

# Pre-download the default embedding model at build time so first request is fast.
# Skipped when INSTALL_ML=false (model would not be available anyway).
# Override EMBEDDING_MODEL at runtime to use a different model (rebuild required).
RUN if [ "$INSTALL_ML" = "true" ]; then \
        python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"; \
    fi
# Prevent any HuggingFace Hub network calls at runtime; rely solely on the cache above.
ENV HF_HUB_OFFLINE=1

COPY . ./api/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
