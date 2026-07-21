---
paths:
  - "api/services/mcp/**"
  - "api/routers/mcp.py"
---

# MCP server (embedded)

EmbedBase embeds a **FastMCP** server inside the FastAPI app so an external agent (Claude Code/Desktop,
Cursor, Zed) can drive the store. It exposes **five tools**, no resources/prompts:
`list_workspaces`, `search_documents`, `ingest_document` (container-local file path), `list_documents`,
`delete_document`.

## Key files
- `api/services/mcp/server.py` — builds the `FastMCP` server, **declares the tools**, resolves runtime deps,
  builds + mounts the ASGI app. The central file.
- `api/services/mcp/tools.py` — framework-agnostic tool **implementations**; thin wrappers over existing services.
- `api/services/mcp/middleware.py` — raw ASGI middleware: API-key auth (resolves the caller's `Principal`) +
  rate limit on `/mcp`.
- `api/services/mcp/context.py` — a `ContextVar` carrying the authenticated `Principal` from the middleware to
  the tool wrappers (`current_principal()`).
- `api/services/mcp/rate_limit.py` — per-key `TokenBucketRateLimiter`.
- `api/routers/mcp.py` — routing-only shim; `mount_mcp()` delegates to the service.
- `MCPConfig` (`enabled`, `rate_limit_rpm`, `max_results`) in `api/models/config.py`; mounted last in `api/main.py`.

## Adding / changing a tool — two layers, always both
1. **Implement** in `tools.py` as an `async def`, keyword-only, that **delegates to an existing service** and
   returns a JSON-serialisable `dict`. It receives deps (`db`, `embedder`, `vector_store`, `reranker`,
   `principal`) as kwargs — it never resolves them itself and never opens a DB session.
2. **Declare** in `server.py::_register_tools()` with `@server.tool()`. The wrapper's **signature = the tool's
   input schema** and its **docstring = the description the LLM reads** — both are load-bearing. The wrapper
   resolves singletons via `_require(get_embedding_adapter(), …)` / `get_vector_store()` / `get_reranker()`
   from `api/dependencies.py`, reads the caller via `current_principal()`, opens
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
  (DB-backed, via `authenticate_api_key`) and binds it for the request (`context.py`). The tool impls enforce
  that user's **grants**: `list_workspaces` is filtered (`filter_workspace_tree`), `search_documents` narrows to
  readable collections (`readable_collection_ids`, 403 if none), `list_documents` needs `read`, `delete` needs
  `write` (`authorize_document`). `ingest_document` references an arbitrary container-local path, so it is
  **master-only** (an operator capability — a scoped write grant is not enough; it could copy any
  server-reachable file into the caller's collection). A denied action returns a tool error (403). Master
  bypasses every check. Over-limit → 429. See [`permissions.md`](permissions.md).
- `_require()` raises `RuntimeError("<X> backend not ready")` if the embedding/vector-store singleton hasn't
  warmed up yet; the reranker is optional (`None` just skips rerank). `search_documents` clamps `top_k` to
  `[1, mcp.max_results]` and emits a `more_available` + `notice` saturation hint when relevant chunks exceed the cut.

## Delegates to (point here, don't duplicate)
`workspaces.list_workspace_tree`, `search.multi_collection_search`, `documents.{ingest_local_path,list_documents,
delete_document,resolve_document_collection}`, `permissions.{authorize_collection,authorize_document,
readable_collection_ids,filter_workspace_tree}`, `auth.Principal`. (Distinct from the standalone REST reference
at `/api/reference` — don't conflate them.)
