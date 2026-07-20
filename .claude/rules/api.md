---
paths:
  - "api/routers/**"
  - "api/schemas/**"
  - "api/main.py"
  - "api/middleware.py"
  - "api/dependencies.py"
---

# HTTP API

FastAPI app built in `api/main.py` (`root_path="/api"` — nginx strips the prefix). Layering is strict and
one-way: **`router → service → (adapter | db)`** (see [`architecture.md`](architecture.md)).

## Routers stay thin
`api/routers/*.py` do path registration + `Depends(...)` + an authorization check, then a **single
delegating call** to a service. No business logic, no raw SQL, no schema declarations. The routers:

| Router | Base | Auth | Responsibility |
|--------|------|------|----------------|
| `health` | `/healthz`, `/metrics` | none | liveness snapshot |
| `workspaces` | `/workspaces` | router `require_master` | workspace CRUD |
| `collections` | `/workspaces/{ws}/collections` | router `require_master` | collection CRUD + API-key mgmt |
| `documents` | nested + flat `/documents…` | per-route `require_auth` + `can_access` | upload / list / status / delete / download / reprocess |
| `tags` | `/workspaces/{ws}` | router `require_master` | tag CRUD, merge, assignment |
| `graph` | `/workspaces/{ws}` | router `require_master` | tag-correlation graph |
| `search` | `POST /search` | per-route `require_master` | multi-collection hybrid search |
| `indexing` | `/indexing/…` | mixed master/auth | BM25 (re)index status + enqueue |
| `jobs` | `/ingestion/jobs…` | `require_master` | ingestion-queue list, stats, retry-failed |
| `config` | `/config` | router `require_master` | live config GET/PUT, ollama-models, reload-status |
| `ws` | `WS /ws` | `?key=` query param | Redis pub/sub → WebSocket (ingestion progress) |
| `mcp` | mounted `/mcp` | in middleware | MCP ASGI sub-app, mounted **last** ([`mcp.md`](mcp.md)) |

## Adding an endpoint
1. **Route** → the matching `api/routers/<domain>.py`; handler resolves/authorizes then `return await <svc>.<fn>(...)`.
2. **Model** → CRUD request bodies go in `api/schemas/<domain>.py`; richer domain/response/query models and
   config go in `api/models/<domain>.py`. (`schemas/` = per-endpoint DTOs; `models/` = shared domain/config
   contracts reused across layers. `api/tables/` is persistence, not Pydantic.)
3. **Logic + DB** → new function in `api/services/<domain>.py` (`select(...)` on the injected `AsyncSession`;
   raise `HTTPException` on domain errors — [`database.md`](database.md)).
4. **Register** in `api/main.py` (`include_router`); keep the MCP mount last.
5. **Inject deps** from `api/dependencies.py`: `db = Depends(get_db)`; auth `require_master` / `require_auth`
   ([`permissions.md`](permissions.md)); adapters `Depends(require_embedding_adapter | require_vector_store)`
   (503 if not ready) or `Depends(get_reranker)` (optional, may be `None`); config `Depends(get_search_config | get_app_config)`.

## Errors, middleware, lifespan
- Services raise `fastapi.HTTPException(status, detail)` → `{"detail": …}`. Codes in use: 401 (bad/missing key),
  403 (wrong-collection / master-required), 404, 409 (conflict), 415 (unsupported file type), 422 (validation),
  503 (backend/queue not ready). No custom global handlers — FastAPI defaults + the request-id middleware.
- **Graceful degradation is a hard rule**: an optional stage that lacks a model / errors falls back and
  **never 500s** a search or ingest.
- Middleware (`api/middleware.py`): `RequestIDMiddleware` (outermost) mints a `uuid4` request id, times the
  request, sets `X-Request-ID`, emits one structured log line; CORS inside it.
- Lifespan (`api/main.py`): structlog → load `config.yaml` → `init_db()` (migrations) → **background**
  `_warm_up_adapters` (embedding + vector-store + reranker load off the startup path, so uvicorn serves
  immediately; getters return `None` and `/healthz` reports not-loaded until ready) → Redis → run the MCP
  session manager for the app's lifetime.
