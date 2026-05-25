FROM python:3.12-slim

WORKDIR /app

# System deps for PyMuPDF, tree-sitter grammars, and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default embedding model weights into the image layer
# so the first container start does not need internet access
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
