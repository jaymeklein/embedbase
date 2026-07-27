---
paths:
  - "api/services/mcp/**"
  - "api/routers/mcp.py"
---

# MCP server (embedded)

EmbedBase embeds a **FastMCP** server inside the FastAPI app so an external agent (Claude Code/Desktop,
Cursor, Zed) can drive the store **entirely from chat** — a scoped user never needs the console for their
day-to-day work. No resources/prompts, just tools, grouped by domain:

- **Read/search:** `list_workspaces`, `search_documents`, `list_documents`, `get_document_chunks`.
- **Document lifecycle:** `request_upload` + `confirm_upload` (the presigned two-step upload — see below),
  `ingest_document` (container-local path, **master-only**), `download_document` (presigned GET URL),
  `get_document_status`, `reprocess_document`, `delete_document`.
- **Structure CRUD:** `create_workspace` / `update_workspace` / `delete_workspace`,
  `create_collection` / `update_collection` / `delete_collection`.
- **Tags:** `list_tags` / `create_tag` / `update_tag` / `delete_tag` / `merge_tags`,
  `assign_tag` / `unassign_tag` (a `target` = workspace|collection|document discriminator).
- **Ops/observability:** `list_ingestion_jobs`, `get_ingestion_stats`, `get_rate_limit`.

Admin-plane concerns (app **config**, **user/key/grant** management) stay console/config-file only — **not**
exposed via MCP.

## Key files
- `api/services/mcp/server.py` — builds the `FastMCP` server, **declares the tools**, resolves runtime deps,
  builds + mounts the ASGI app. The central file. Registration is split into per-domain helpers
  (`_register_tools` [core read/search], `_register_document_lifecycle_tools`, `_register_structure_tools`,
  `_register_tag_tools`, `_register_ops_tools`); `build_mcp_server` calls the core one then iterates
  `_DOMAIN_REGISTRARS` — **the registrar a tool sits in IS its catalogue group**, so add a tool to the right
  `_register_*` and it groups correctly everywhere. `build_tool_catalog()` probes those same helpers to emit
  the grouped catalogue (name + synthesized signature + one-line summary) for **`GET /mcp-tools`**
  (`api/routers/mcp.py`, `require_auth`), which the **Settings → MCP** page + its generated SKILL.md render
  from — so the console tool list never drifts from the registered tools. No hand-kept mirror.
- `api/services/mcp/tools.py` — framework-agnostic tool **implementations**, sectioned by domain; thin
  wrappers over existing services.
- `api/services/mcp/middleware.py` — raw ASGI middleware: API-key auth (resolves the caller's `Principal`) +
  rate limit on `/mcp`; pushes the authenticated user's per-key rate-limit override into the limiter
  (`set_key_limit`, post-auth) and binds the caller's post-throttle rate-limit snapshot for `get_rate_limit`.
- `api/services/mcp/context.py` — `ContextVar`s carrying the authenticated `Principal` **and** the rate-limit
  snapshot from the middleware to the tool wrappers (`current_principal()` / `current_rate_limit()`).
- `api/services/mcp/rate_limit.py` — per-key `TokenBucketRateLimiter` (`allow()` consumes; `snapshot()` is a
  non-consuming budget read for `get_rate_limit`). Each key's rpm is the global `mcp.rate_limit_rpm` unless a
  **per-key override** is set via `set_key_limit` — the middleware pushes the caller's `users.rate_limit_rpm`
  post-auth (`0` = inherit).
- `api/routers/mcp.py` — routing-only shim; `mount_mcp()` delegates to the service.
- `MCPConfig` (`enabled`, `rate_limit_rpm`, `max_results`) in `api/models/config.py`; mounted last in `api/main.py`.

## File upload — presigned two-step (there's no shared filesystem over MCP)
The store is MinIO/S3, so a scoped user uploads bytes **directly to storage**, never through the JSON-RPC
tool call: `request_upload` (`documents.create_upload`, needs collection **write**) reserves a document row
with status `awaiting_upload` and returns a **presigned PUT `upload_url`**; the client `PUT`s the file to it;
`confirm_upload` (`documents.confirm_upload`, document **write**) verifies the object landed (`object_head`),
re-enforces the size cap, flips the row active, and enqueues ingestion. An `awaiting_upload` row is hidden
from every listing/count/search until confirmed, is cancelable via `delete_document`, and an abandoned one is
reaped by the worker purge sweep (`_AWAITING_UPLOAD_TTL_HOURS`). The presigned PUT URL is reusable within its
validity, so the **worker re-checks the object size before fetching** (`worker/tasks.py`) — a confirm-time
check alone is bypassable. Presigned upload needs an S3/MinIO backend; local-disk deployments upload via REST.

## Adding / changing a tool — two layers, always both
1. **Implement** in `tools.py` as an `async def`, keyword-only, that **delegates to an existing service** and
   returns a JSON-serialisable `dict`. It receives deps (`db`, `embedder`, `vector_store`, `reranker`,
   `principal`) as kwargs — it never resolves them itself and never opens a DB session.
2. **Declare** in the matching `server.py::_register_*_tools()` helper with `@server.tool()`. The wrapper's
   **signature = the tool's input schema** and its **docstring = the description the LLM reads** — both are
   load-bearing. The wrapper resolves singletons via `_require(get_embedding_adapter(), …)` /
   `get_vector_store()` / `get_reranker()` / `get_redis_client()` from `api/dependencies.py`, reads the caller
   via `current_principal()` (and `current_rate_limit()` for `get_rate_limit`), opens
   `async with AsyncSessionLocal() as db:`, then calls the `tools.*` function passing `principal=…`. The impl
   enforces that principal's grants (`permissions.authorize_*`) before touching data.

Don't put business logic in the wrapper or the router; don't resolve deps in `tools.py`.

## Transport & mounting (already plumbed — don't re-plumb)
- `FastMCP("embedbase", stateless_http=True, json_response=True)`, `streamable_http_path="/"` — **stateless
  streamable HTTP** (verified against `mcp==1.28.1`). Chosen because SSE died on `uvicorn --reload`; **do not
  reintroduce SSE / stateful sessions.**
- `mount_app()` owns the enablement decision (`if not config.enabled: return`), mounts at `/mcp`, stashes the
  server on `app.state.mcp_server`. `api/main.py` runs `mcp_server.session_manager.run()` for the app's
  lifetime (`nullcontext` when disabled), and mounts MCP **last** so `/mcp` never shadows REST routes.
- External URL is `/api/mcp/` (app `root_path="/api"` + sub-app at `/mcp`); the trailing slash avoids a 307.

## Auth & per-user enforcement
- Every request must carry a valid key — the **master key** or an **active user's key** (`Authorization: Bearer`
  or `X-API-Key`); missing/invalid → 401, inactive user → 403. `middleware.py` resolves the caller's `Principal`
  (DB-backed, via `authenticate_api_key`) and binds it for the request (`context.py`). Each tool impl enforces
  that user's **grants** before touching data, mirroring the REST routers:
  - **Reads** — `list_workspaces` filtered (`filter_workspace_tree`); `search_documents` narrows to readable
    collections (`readable_collection_ids`, 403 if none); `list_documents` / `get_document_status` /
    `download_document` / `get_document_chunks` need `read` on the document/collection.
  - **Document writes** — `request_upload` needs collection `write`; `confirm_upload` / `reprocess_document` /
    `delete_document` need document `write`.
  - **Structure CRUD** — grant-scoped like the routers: `create_workspace` needs the `create_workspace`
    capability (+ `grant_creator_access`); workspace/collection edit+delete need `write` on the resource;
    `create_collection` needs workspace `write`.
  - **Tags** — the `manage_tags` capability (`authorize_tag_management`) gates all tag tools; `assign_tag` /
    `unassign_tag` additionally need `write` on a collection/document target.
  - **Ops** — `list_ingestion_jobs` / `get_ingestion_stats` scope to `readable_collection_scope`;
    `get_rate_limit` reports only the caller's own bucket (`current_rate_limit()`) + the global embedding pause.
  - **Exception:** `ingest_document` references an arbitrary container-local path, so it is **master-only** (an
    operator capability — a scoped write grant is not enough; it could copy any server-reachable file into the
    caller's collection). Scoped users upload via `request_upload` instead.
  A denied action returns a tool error (403). Master bypasses every check. Over-limit → 429. See
  [`permissions.md`](permissions.md).
- `_require()` raises `RuntimeError("<X> backend not ready")` if the embedding/vector-store singleton hasn't
  warmed up yet; the reranker is optional (`None` just skips rerank). `search_documents` clamps `top_k` to
  `[1, mcp.max_results]` and emits a `more_available` + `notice` saturation hint when relevant chunks exceed the cut.

## Delegates to (point here, don't duplicate)
`workspaces.{list_workspace_tree,create_workspace,update_workspace,delete_workspace}`,
`collections.{create_collection,update_collection,delete_collection}`, `search.multi_collection_search`,
`documents.{create_upload,confirm_upload,ingest_local_path,list_documents,get_document_status,
resolve_download_url,delete_document,reprocess_document,resolve_document_collection}`,
`tags.{list_tags,create_tag,update_tag,delete_tag,merge_tags,assign_*,unassign_*}`,
`jobs.{list_jobs,job_status_counts,embedding_pause_seconds}`, the vector store's `document_chunks`,
`permissions.{authorize_collection,authorize_document,authorize_workspace,authorize_workspace_creation,
grant_creator_access,authorize_tag_management,readable_collection_ids,readable_collection_scope,
filter_workspace_tree}`, `auth.Principal`. (Distinct from the standalone REST reference at `/api/reference` —
don't conflate them.)
