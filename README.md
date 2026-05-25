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

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "embedbase": {
      "command": "curl",
      "args": ["-N", "http://localhost:8000/mcp/sse"],
      "env": {
        "AUTHORIZATION": "Bearer <your-collection-api-key>"
      }
    }
  }
}
```

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
