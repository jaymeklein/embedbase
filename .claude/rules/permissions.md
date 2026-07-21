---
paths:
  - "api/services/auth.py"
  - "api/services/permissions.py"
  - "api/services/users.py"
  - "api/tables/api_keys.py"
  - "api/tables/users.py"
  - "api/tables/permissions.py"
  - "api/routers/users.py"
  - "api/services/mcp/middleware.py"
  - "api/services/mcp/rate_limit.py"
  - "api/routers/ws.py"
---

# Permissions — users, API keys, and grants

**API keys only — no sessions/cookies/JWT.** Two credentials: the **master key** (admin/bootstrap) and a
**user key** (one per user). What a user key may reach is decided by **grants** (`read`/`write`) over the
`workspace → collection → document` hierarchy. The auth core is `api/services/auth.py`; the single
authorization authority is `api/services/permissions.py`.

## Credentials
- **Master key** — the one required secret `MASTER_API_KEY` (`api/settings.py`; app won't start without it).
  Bypasses every check (any workspace/collection/document; the whole management plane). Matched with
  `secrets.compare_digest` (constant-time).
- **User key** — an `eb_`-prefixed token owned by a **user** (`api_keys.user_id`, `UNIQUE(user_id)` → exactly
  one key each). Stored as `key_prefix` (chars `raw[3:11]`, indexed) + **bcrypt** `key_hash`; auth narrows by
  prefix then `bcrypt.checkpw`. The raw key is returned **once** at mint and never again. A key whose user is
  **inactive** (`users.is_active = False`) is rejected with `403`.
- Header: `X-API-Key` (preferred) or `Authorization: Bearer <key>`. WebSocket can't set headers → `?key=`
  query param (`api/routers/ws.py`). Auth resolves to a frozen `Principal(is_master, user_id, api_key_id)`.

## Grants (`permissions` table)
A grant gives one user a `level` (`read` | `write`) on one resource (`workspace` | `collection` | `document`).
Grants are **hierarchical** and `write` implies `read`:
- a **workspace** grant covers its collections and documents;
- a **collection** grant covers its documents;
- `read` = search / list / download; `write` = ingest / delete (and read).

`resource_id` is a plain string (no FK — polymorphic across three tables, like `job_records`); a grant left
dangling by a deleted resource is harmless (uuids are never reissued).

## Enforcing access — `api/services/permissions.py` is the only authority
Routers and MCP tools call it and let it raise `403`; **never** re-implement the check inline:
- `authorize_collection(db, principal, col_id, need)` / `authorize_document(db, principal, doc_id, need)` —
  master passes; a user passes iff a `need`-satisfying grant exists on the resource **or an ancestor**.
- `readable_collection_ids(db, principal, ids)` — narrows a candidate list (used by `/search` and the MCP
  `search_documents`; 403 when none survive).
- `filter_workspace_tree(db, principal, tree)` — prunes the MCP `list_workspaces` tree to readable nodes.

Two FastAPI deps stay in `auth.py`: `require_master` (403 unless `is_master`) and `require_auth` (master or
user key; records `last_used_at`). Patterns:
- **Management planes** → router-level gate `APIRouter(..., dependencies=[Depends(require_master)])`
  (workspaces, collections, tags, config, graph, **users**). Creating workspaces/collections/keys/grants is
  **master-only** — users get the data plane, not the management plane.
- **Data-plane route** → `require_auth` then `await permissions.authorize_*(db, principal, ..., need)`
  (documents, indexing, search, the ws bridge). `/search` authorizes each requested collection for `read`.

## Users management API (`api/routers/users.py`, `api/services/users.py` — master-only)
CRUD users (create/list/get/update/delete), activate/deactivate (`is_active`), mint/rotate/revoke the user's
one key (`POST|DELETE /users/{id}/key`; mint returns the raw key once, and **replaces** any existing key —
that's rotation), and grant CRUD (`GET|POST /users/{id}/permissions`, `DELETE …/{grant_id}`, delegating to
the permissions service). Key minting lives in `users.py` — the sole place keys are created.

## MCP
The MCP transport authenticates the caller's key (master or active user) in `api/services/mcp/middleware.py`
and binds the resolved `Principal` for the request (`api/services/mcp/context.py`); each tool enforces that
principal's grants (`api/services/mcp/tools.py`) and returns a tool error (403) when denied. **Exception:**
`ingest_document` references an arbitrary container-local path (`documents.ingest_local_path`), so it is
**master-only** — a scoped write grant must not become an arbitrary server-file read / cross-tenant copy.
Over-limit → 429 (per-key token bucket). See [`mcp.md`](mcp.md).

## Hard rules / gotchas
- **Never return or log `key_hash` or a raw key.** Hashing is fixed: bcrypt, `rounds=12`; the prefix is only a
  candidate filter — never authenticate on prefix alone. Auth errors are coarse (401 missing/invalid vs 403
  inactive/unauthorized) — don't leak which part failed.
- **One authority**: resource decisions go through `permissions.authorize_*` — don't add ad-hoc scope checks in
  routers/tools. There is no `Principal.can_access` anymore.
- `record_key_use` now runs in `require_auth` and the MCP middleware, so `last_used_at` is live (was a known gap).
- Config secrets are masked separately ([`configuration.md`](configuration.md)) — same "never expose secrets"
  rule, different code path.
