FROM python:3.12-slim

WORKDIR /app

# System deps for PyMuPDF, tree-sitter grammars, and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default embedding model at build time so first request is fast.
# Override EMBEDDING_MODEL at runtime to use a different one (rebuild required).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
# Prevent any HuggingFace Hub network calls at runtime; rely solely on the cache above.
ENV HF_HUB_OFFLINE=1

COPY . ./api/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
