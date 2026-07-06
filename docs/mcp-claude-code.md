# Connecting Claude Code to the EmbedBase MCP server

This guide shows how to register EmbedBase's MCP server with
[Claude Code](https://claude.com/claude-code) using `claude mcp add`, so an
agent can search, ingest, and manage your collections directly from a coding
session.

EmbedBase exposes five tools over an **SSE** transport: `list_workspaces`,
`search_documents`, `ingest_document`, `list_documents`, and `delete_document`.

---

## Prerequisites

- The stack is running (`docker compose up`) and the **API is reachable** at
  `http://localhost:8000` (or your `API_PORT`). Check with
  `curl http://localhost:8000/healthz`.
- The embedding provider is up. With the default provider (**Ollama on the
  host**), make sure `ollama serve` is running and the model is pulled
  (`ollama pull embeddinggemma`) — otherwise `search_documents` fails.
- You have your `MASTER_API_KEY` (from `.env`). It authenticates every MCP
  request.
- `mcp.enabled: true` in `config.yaml` (the default).

## The endpoint

Use the **API port directly**, including the `/api` prefix in the path:

```
http://localhost:8000/api/mcp/sse
```

> **Why `/api/mcp/sse` and not `/mcp/sse`?** The API runs with FastAPI
> `root_path="/api"` (so Swagger and the OpenAPI reference resolve correctly
> behind the reverse proxy). The MCP server is a *mounted* sub-app, and the
> path only routes to it when the request carries the `/api` prefix. Hitting the
> API port at `/mcp/sse`, or going through the Nginx/Vite `/mcp/` proxy, returns
> **404 Not Found**. See [Troubleshooting](#troubleshooting).

## Add it with `claude mcp add`

Run this from anywhere (the key never needs to be typed inline — read it from
`.env`):

```bash
# Read the key from .env into a shell variable so it isn't echoed
KEY=$(grep '^MASTER_API_KEY=' .env | cut -d= -f2)

claude mcp add --transport sse embedbase \
  http://localhost:8000/api/mcp/sse \
  --header "Authorization: Bearer $KEY"
```

- `--transport sse` — EmbedBase speaks SSE, not stdio or streamable-http.
- `--header "Authorization: Bearer <key>"` — sent on every request. You can use
  `X-API-Key: <key>` instead if you prefer.
- `-s, --scope` — where the registration is stored:
  - `local` (default) — private to you, scoped to this project. Best for a
    personal setup.
  - `project` — written to a shared `.mcp.json` committed with the repo.
  - `user` — available across all your projects.

## Verify

```bash
claude mcp get embedbase
```

Expect `Status: ✓ Connected`:

```
embedbase:
  Scope: Local config (private to you in this project)
  Status: ✓ Connected
  Type: sse
  URL: http://localhost:8000/api/mcp/sse
  Headers:
    Authorization: Bearer ...
```

`claude mcp list` shows it alongside your other servers. If a session was
already open when you added it, restart the session (or reconnect) so the new
server loads.

## Use it

A typical flow inside a session:

1. **`list_workspaces()`** — discover workspace/collection IDs. Never invent
   them; always resolve real IDs here first.
2. **`search_documents(query, collection_ids, top_k=5, hybrid=true, filters?)`**
   — hybrid semantic + BM25 search across one or more collections.
3. **`list_documents(collection_id)`** — inspect a collection's documents and
   their ingestion status.
4. **`ingest_document(collection_id, file_path)`** — ingest a
   **container-local** path (a file the API container can see).
5. **`delete_document(document_id)`** — soft-delete and enqueue vector + BM25
   cleanup.

## Auth & rate limits

- Every request must carry the master key (`Authorization: Bearer …` or
  `X-API-Key: …`). A missing or wrong key returns **401**.
- Requests are rate-limited per key — **60 requests/min** by default
  (`mcp.rate_limit_rpm` in `config.yaml`). The 61st within a minute returns
  **429**.

## Remote / LAN access

To drive EmbedBase from an agent on another machine, point the URL at the host's
LAN address instead of `localhost`:

```
http://<host-lan-ip>:8000/api/mcp/sse
```

Set `LAN_HOST` in `.env` so the server advertises a reachable address (a
bridge-networked container can't detect the host's LAN IP itself). Because the
server is now reachable beyond your own machine, keep `MASTER_API_KEY` strong
and set `EMBEDBASE_SECURE_HEADERS=true`. DNS-rebinding protection in the MCP
transport only allows `localhost`, `127.0.0.1`, and `[::1]` hosts by default; a
different hostname may be rejected.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `✗ Failed to connect`, `404 Not Found` | Wrong path — used `/mcp/sse` or a proxied `/mcp/` URL | Use `http://localhost:8000/api/mcp/sse` (API port, with the `/api` prefix). |
| `401 Missing or invalid API key` | Key not sent or wrong | Check the `Authorization`/`X-API-Key` header matches `MASTER_API_KEY`. |
| `429 Rate limit exceeded` | > 60 req/min on one key | Back off, or raise `mcp.rate_limit_rpm` in `config.yaml`. |
| Connection refused | Stack not up / wrong port | `docker compose ps`; confirm the API port and `curl /healthz`. |
| `search_documents` errors / 500 | Embedding provider down | Start Ollama (`ollama serve`) and `ollama pull embeddinggemma`. |

To change the URL or key later, remove and re-add:

```bash
claude mcp remove embedbase -s local
claude mcp add --transport sse embedbase http://localhost:8000/api/mcp/sse \
  --header "Authorization: Bearer $KEY"
```

## See also

- [MCP section in the README](../README.md#mcp-claude-desktop--cursor--zed) —
  Claude Desktop / Cursor / Zed setup via `mcp-remote`.
- The **Settings → MCP** page in the UI generates a client-config snippet and a
  downloadable `SKILL.md`, and links to the standalone REST reference
  (`/api/reference`).
