# Connecting Claude Code to the EmbedBase MCP server

This guide shows how to register EmbedBase's MCP server with
[Claude Code](https://claude.com/claude-code) using `claude mcp add`, so an
agent can run EmbedBase **entirely from chat** — search, upload/download, manage
workspaces, collections, tags and documents, and check ingestion status — without
opening the console.

EmbedBase exposes a full self-service tool surface over a **streamable HTTP**
transport, grouped by domain:

- **Search / read** — `list_workspaces`, `search_documents`, `list_documents`, `get_document_chunks`
- **Documents** — `request_upload` + `confirm_upload` (presigned upload), `request_original_upload` +
  `confirm_original_upload` (attach an optional original source file), `download_document`,
  `get_document_status`, `reprocess_document`, `delete_document`, `ingest_document` (container path, master-only)
- **Structure** — `create_workspace` / `update_workspace` / `delete_workspace`,
  `create_collection` / `update_collection` / `delete_collection`
- **Tags** — `list_tags` / `create_tag` / `update_tag` / `delete_tag` / `merge_tags`,
  `assign_tag` / `unassign_tag` (needs the `manage_tags` permission)
- **Ops** — `list_ingestion_jobs`, `get_ingestion_stats`, `get_rate_limit`

Every tool acts as **your user** and respects your read/write grants — you only see and change what your
key is allowed to. (Admin settings — app config, user & key management — stay in the console.)

> **Transport note:** EmbedBase previously spoke **SSE**. It now uses
> **streamable HTTP** (`type: http`), which is request-scoped — there is no
> long-lived stream to drop, so it survives API reloads and reverse proxies
> cleanly. If you have an old `--transport sse` registration pointing at
> `/api/mcp/sse`, remove it and re-add it as shown below.

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

Use the **API port directly**, including the `/api` prefix and the trailing
slash:

```
http://localhost:8000/api/mcp/
```

> **Why `/api/mcp/` and not `/mcp/`?** The API runs with FastAPI
> `root_path="/api"` (so Swagger and the OpenAPI reference resolve correctly
> behind the reverse proxy). The MCP server is a *mounted* sub-app, and the path
> only routes to it when the request carries the `/api` prefix — hitting the API
> port at `/mcp/` returns **404**. The trailing slash avoids a `307` redirect.
>
> You can also reach it **through the console** at `http://localhost:3636/api/mcp/`
> (or your console host) — the Vite/Nginx proxy forwards `/api/mcp` verbatim. This
> is what the **Settings → MCP** page in the UI generates, and it's the address to
> use from another machine on your LAN.

## Add it with `claude mcp add`

Run this from anywhere (the key never needs to be typed inline — read it from
`.env`):

```bash
# Read the key from .env into a shell variable so it isn't echoed
KEY=$(grep '^MASTER_API_KEY=' .env | cut -d= -f2)

claude mcp add --transport http embedbase \
  http://localhost:8000/api/mcp/ \
  --header "Authorization: Bearer $KEY"
```

- `--transport http` — EmbedBase speaks streamable HTTP (not `sse` or `stdio`).
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
  Type: http
  URL: http://localhost:8000/api/mcp/
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
   — the primary retrieval tool: hybrid semantic + BM25 search across one or more collections, returning
   only the most relevant chunks (bounded by `top_k`). Start here for any content question; raise `top_k`
   or re-run when the response sets `more_available`.
3. **`list_documents(collection_id)`** / **`get_document_chunks(document_id, chunk_ids?, limit?,
   offset?)`** — inspect a collection's documents (with ingestion status), or how a document was
   chunked: pass the `chunk_id`s from a search result to pull just those chunks (≤100), or omit
   them to page through the whole document (`limit` 1–100 + `offset`, with `total`/`has_more`).
   `get_document_chunks` is a follow-up to search, not a document reader — paging every chunk of a large
   document pulls in irrelevant text; let search rank what matters.
4. **Upload a file (presigned, two steps):**
   - **`request_upload(collection_id, filename, retention_days?)`** → returns an `upload_url` +
     `document_id`. Set `retention_days` (1–30) to auto-delete the file after that many days; omit for permanent.
   - `PUT` the file bytes to `upload_url` (e.g. `curl -X PUT --upload-file ./f.pdf "<upload_url>"`).
   - **`confirm_upload(document_id)`** → verifies the upload and starts ingestion.
   - **Optionally keep the original:** to store the source file alongside the parse (e.g. the raw PDF a
     Markdown upload was converted from), `request_original_upload(document_id, filename)` → `PUT` the bytes →
     `confirm_original_upload(document_id)`. It's never embedded; fetch it later with
     `download_document(document_id, original=true)`.

   (`ingest_document(collection_id, file_path)` is the master-only shortcut for a file already on the API
   container's disk.)
5. **`get_document_status(document_id)`** — check ingestion progress; **`reprocess_document(document_id)`**
   to retry a failed one; **`download_document(document_id, original?)`** for a short-lived download URL
   (set `original=true` for the attached original source file).
6. **`delete_document(document_id)`** — soft-delete and enqueue vector + BM25 cleanup.
7. **Manage structure & tags** — `create_workspace` / `create_collection` (+ update/delete), and the tag
   tools (`create_tag`, `assign_tag`, …) when your key holds the `manage_tags` permission.
8. **`get_ingestion_stats()`** / **`get_rate_limit()`** — queue depth + whether ingestion is paused on a
   provider quota, and your remaining MCP call budget with the time until it refills.

## Auth & rate limits

- Every request must carry the master key (`Authorization: Bearer …` or
  `X-API-Key: …`). A missing or wrong key returns **401**.
- Requests are rate-limited per key — **60 requests/min** by default. The 61st
  within a minute returns **429**. Raise the ceiling in **Settings → Config →
  MCP server**: the limiter re-reads the limit on every request, so a save binds
  on the next call with no restart. Editing `mcp.rate_limit_rpm` in `config.yaml`
  by hand works too, but nothing re-reads the file at runtime — that route only
  takes effect when the API restarts.

## Remote / LAN access

To drive EmbedBase from an agent on another machine, point the URL at the host's
LAN address. The console proxy is the reachable single entry point:

```
http://<host-lan-ip>:3636/api/mcp/
```

(or `:8000` if you expose the API port on the LAN). Set `LAN_HOST` in `.env` so
the server advertises a reachable address (a bridge-networked container can't
detect the host's LAN IP itself). Because the server is now reachable beyond your
own machine, keep `MASTER_API_KEY` strong and set
`EMBEDBASE_SECURE_HEADERS=true`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `✗ Failed to connect`, `404 Not Found` | Wrong path — used `/mcp/` or dropped the `/api` prefix | Use `http://localhost:8000/api/mcp/` (API port, `/api` prefix, trailing slash), or `http://<console>/api/mcp/`. |
| Registered as `sse` / old `/api/mcp/sse` URL | Stale SSE registration | Remove and re-add with `--transport http` and the `/api/mcp/` URL (below). |
| `401 Missing or invalid API key` | Key not sent or wrong | Check the `Authorization`/`X-API-Key` header matches `MASTER_API_KEY`. |
| `429 Rate limit exceeded` | > 60 req/min on one key | Back off, or raise the limit in Settings → Config → MCP server (applies immediately). |
| Connection refused | Stack not up / wrong port | `docker compose ps`; confirm the API port and `curl /healthz`. |
| `search_documents` errors / 500 | Embedding provider down | Start Ollama (`ollama serve`) and `ollama pull embeddinggemma`. |

To change the URL or key later, remove and re-add:

```bash
claude mcp remove embedbase -s local
claude mcp add --transport http embedbase http://localhost:8000/api/mcp/ \
  --header "Authorization: Bearer $KEY"
```

## See also

- [MCP section in the README](../README.md#mcp-claude-desktop--cursor--zed) —
  Claude Desktop / Cursor / Zed setup.
- The **Settings → MCP** page in the UI generates a client-config snippet and a
  downloadable **skill bundle** (`SKILL.md` + `references/`, as a zip), and links to the
  standalone REST reference (`/api/reference`).
