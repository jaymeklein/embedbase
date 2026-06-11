# EmbedBase

A local-first, open-source document embedding system. Ingest documents, search them semantically, and expose results via REST API and MCP server — all without data leaving your machine.

## Quickstart

```bash
# Clone inside WSL2 (not /mnt/c — see WSL2 notes below)
git clone https://github.com/your-org/embedbase
cd embedbase

# Configure
cp .env.example .env
# Edit .env: set MASTER_API_KEY to a random 32+ char string
# e.g. python -c "import secrets; print(secrets.token_urlsafe(32))"

cp config.example.yaml config.yaml  # already done if this file exists

# Start the stack (downloads ~90MB model on first run)
docker compose up --build

# UI:  http://localhost:3000
# API: http://localhost:8000
# Docs: http://localhost:8000/docs
```

## Vector store backends

```bash
# Default — Chroma
docker compose up

# pgvector (Postgres 16)
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up

# Qdrant
docker compose -f docker-compose.yml -f docker-compose.qdrant.yml up
```

## MCP (Claude Desktop / Cursor / Zed)

EmbedBase exposes an MCP server over SSE at `http://localhost:8000/mcp/sse`
(proxied by Nginx at `/mcp/`). Claude Desktop talks to a *remote* SSE server via
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote). Add to
`~/.config/claude/claude_desktop_config.json` (or `%APPDATA%\Claude\claude_desktop_config.json`
on Windows):

```json
{
  "mcpServers": {
    "embedbase": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "http://localhost:8000/mcp/sse",
        "--header", "Authorization: Bearer ${EMBEDBASE_MASTER_KEY}"
      ],
      "env": {
        "EMBEDBASE_MASTER_KEY": "<your MASTER_API_KEY>"
      }
    }
  }
}
```

Authenticate with your `MASTER_API_KEY`. Each key is limited to 60 requests/min
(configurable via `mcp.rate_limit_rpm`); the 61st in a minute returns `429`.

**Tools:** `list_workspaces`, `search_documents` (`query`, `collection_ids[]`,
`top_k`, `hybrid`, `filters`), `ingest_document` (container-local path),
`list_documents`, `delete_document`.

## Document parsers (OCR, DOCX/PPTX, optional GPU)

PDFs default to the fast PyMuPDF parser (~10 ms/page) — best for text-heavy
documents. For scanned PDFs or table extraction, switch to the
[docling](https://github.com/docling-project/docling) backend in `config.yaml`:

```yaml
parsers:
  pdf_backend: docling   # OCR + table structure (CPU ~200-800 ms/page)
  docling_ocr: true
  docling_tables: true
```

`.docx` and `.pptx` always use docling (no lightweight adapter exists), so they
work as soon as the worker image carries the ML deps. docling models download
lazily on first use; pre-bake them with `--build-arg EMBEDBASE_DOCLING_MODELS=true`.

**GPU acceleration (NVIDIA RTX only)** brings docling to ~30-80 ms/page:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up
```

This requires the NVIDIA Container Toolkit and a CUDA-matched torch build. Check
your driver with `nvidia-smi`, then pick the matching `cu1XX` wheel from
[pytorch.org/get-started/locally](https://pytorch.org/get-started/locally/) and
set it in `worker/Dockerfile.gpu`. Enable the GPU in `config.yaml` with
`parsers.docling_device: cuda` (or `auto` to fall back to CPU when no GPU is
present). The default CPU stack has zero NVIDIA dependencies.

## WSL2 notes

- Clone inside the WSL2 filesystem (`~/`) — not `/mnt/c/`
- Allocate at least 8 GB RAM in `%UserProfile%\.wslconfig`
- Use `host.docker.internal` to reach services on the Windows host (e.g. Ollama)

## Security checklist (shared networks)

- [ ] Put Nginx behind a TLS reverse proxy (Caddy recommended)
- [ ] Set a strong, random `MASTER_API_KEY` (min 32 chars)
- [ ] Remove the `ports` mapping for `api` — Nginx is the only ingress
- [ ] Set `CHROMA_AUTH_TOKEN` to a non-default value
- [ ] Set `EMBEDBASE_SECURE_HEADERS=true`

## License

Apache 2.0
