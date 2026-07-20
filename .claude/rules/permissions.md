---
paths:
  - "api/services/auth.py"
  - "api/tables/api_keys.py"
  - "api/services/collections.py"
  - "api/services/mcp/middleware.py"
  - "api/services/mcp/rate_limit.py"
  - "api/routers/collections.py"
  - "api/routers/ws.py"
---

# Permissions — API-key auth

**API keys only — no sessions/cookies/JWT.** The core is `api/services/auth.py`. Authorization is binary:
**master vs single-collection** (no roles/RBAC/scopes).

## Two credential types
- **Master key** — the one required secret `MASTER_API_KEY` (`api/settings.py`; app won't start without it).
  Grants access to every workspace/collection. Matched with `secrets.compare_digest` (constant-time).
- **Collection key** — an `eb_`-prefixed token scoped to exactly one collection. Stored in the `api_keys`
  table as `key_prefix` (chars `raw[3:11]`, indexed) + **bcrypt** `key_hash`. Auth = narrow by prefix, then
  `bcrypt.checkpw` the full key. The raw key and plaintext are **never** stored.
- Header: `X-API-Key` (preferred) or `Authorization: Bearer <key>`. WebSocket can't set headers → `?key=`
  query param (`api/routers/ws.py`). Auth resolves to a frozen `Principal(is_master, collection_id, api_key_id)`.

## Protecting a route
Two FastAPI deps in `auth.py`:
- `require_master` → `Principal`, 403 unless `is_master`.
- `require_auth` → `Principal` (master **or** collection key); **does not** enforce collection scope.

Patterns:
- **Admin/management planes** → router-level gate: `APIRouter(..., dependencies=[Depends(require_master)])`
  (collections, workspaces, tags, config, graph). This exists so a later-added endpoint can't be accidentally
  unauthenticated.
- **Collection-scoped route** → the canonical shape:
  ```python
  principal: Principal = Depends(require_auth)
  await doc_svc.resolve_collection(db, col_id, ws_id)   # 404 if not in ws
  if not principal.can_access(col_id):
      raise HTTPException(403, "API key not valid for this collection")
  ```
  Forgetting the `can_access` check after `require_auth` leaves a collection key able to touch other
  collections — the scope check is your responsibility per route.
- Note: **search is master-only**; collection keys manage their own collection's documents but can't run search.

## Key lifecycle (`api/services/collections.py`)
- **Mint**: `raw = "eb_" + secrets.token_urlsafe(32)`; store `key_prefix` + `bcrypt.hashpw(raw, gensalt(rounds=12))`.
  The raw key is returned **once** and never retrievable again.
- **List/mask**: metadata only — the query never selects `key_hash`; clients see only `key_prefix`.
- **Revoke**: hard-deletes the row (immediate; no soft-delete/disabled flag).

## Hard rules / gotchas
- **Never return or log `key_hash` or a raw key.** Hashing is fixed: bcrypt, `rounds=12`; prefix is only a
  candidate filter — never authenticate on prefix alone. Auth errors are coarse (401 vs 403) — don't leak which part failed.
- **MCP** runs as master: its transport is gated by `MCPAuthRateLimitMiddleware` (master key only + per-key
  token-bucket rate limit → 429), and tools use `MASTER_PRINCIPAL` ([`mcp.md`](mcp.md)).
- **Known gap**: `record_key_use` (updates `last_used_at`) is currently only called in tests — no router/dep
  invokes it, so `last_used_at` is effectively never updated in the running app. Don't assume usage tracking works.
- Config secrets are masked by a separate mechanism ([`configuration.md`](configuration.md)) — same
  "never expose secrets" rule, different code path. Request-id logging is in `api/middleware.py` (not auth).
