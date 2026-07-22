---
paths:
  - "api/services/auth.py"
  - "api/services/session.py"
  - "api/services/permissions.py"
  - "api/services/users.py"
  - "api/tables/api_keys.py"
  - "api/tables/users.py"
  - "api/tables/permissions.py"
  - "api/routers/users.py"
  - "api/routers/auth.py"
  - "api/schemas/auth.py"
  - "api/routers/workspaces.py"
  - "api/routers/collections.py"
  - "api/services/mcp/middleware.py"
  - "api/services/mcp/rate_limit.py"
  - "api/routers/ws.py"
---

# Permissions — users, sessions, API keys, and grants

**Three credentials, two authz planes.** Credentials: the **master key** (bootstrap), a **console session**
(a user's login JWT), and a **user key** (`eb_`, one per user, for MCP/programmatic access). Access is
either **master-equivalent** (the master key or an **admin** user → the whole management plane) or
**grant-scoped** (a non-admin user → `read`/`write` grants over the `workspace → collection → document`
hierarchy). The auth core is `api/services/auth.py` (+ `session.py` for JWTs); the single authorization
authority is `api/services/permissions.py`. **MCP stays API-key-only — JWTs never reach it.**

## Credentials
- **Master key** — the one required secret `MASTER_API_KEY` (`api/settings.py`; app won't start without it).
  Master-equivalent (bypasses every check). Matched with `secrets.compare_digest` (constant-time).
- **Console session (JWT)** — from `POST /auth/login` (username + bcrypt password). A short-lived **HS256**
  token signed with a key **derived from `master_api_key`** (`api/services/session.py`; no separate secret —
  rotating the master invalidates every session). Sent as `Authorization: Bearer`. An **admin** user
  (`users.is_admin`) resolves master-equivalent; a **non-admin** resolves grant-scoped. `resolve_bearer`
  verifies a JWT first, else falls back to the key path; it re-checks `is_active` + `is_admin` live and the
  token's `pwd_epoch` against `password_changed_at` (a reset/change invalidates prior sessions). A
  **must-change** session (first login / after a reset) may reach *only* `/auth/change-password` + `/auth/me`.
- **User key** — an `eb_`-prefixed token owned by a **user** (`api_keys.user_id`, `UNIQUE(user_id)` → exactly
  one key each). Stored as `key_prefix` (chars `raw[3:11]`, indexed) + **bcrypt** `key_hash`; auth narrows by
  prefix then `bcrypt.checkpw`. The raw key is returned **once** at mint and never again. A key whose user is
  **inactive** (`users.is_active = False`) is rejected with `403`.
- Header: `X-API-Key` (preferred) or `Authorization: Bearer <key-or-jwt>`. WebSocket can't set headers →
  `?key=` query param (`api/routers/ws.py`, which uses `resolve_bearer` so a session works too). Auth resolves
  to a frozen `Principal(is_master, user_id, api_key_id, must_change)` — `is_master` means *master-equivalent*
  (master key **or** admin user).

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
- `authorize_workspace(db, principal, ws_id, need)` / `authorize_collection(...)` /
  `authorize_document(...)` — master/admin passes; a user passes iff a `need`-satisfying grant exists on the
  resource **or an ancestor** (a collection/workspace grant makes ancestors browsable for `read`).
- `readable_workspace_ids(db, principal, ids)` / `readable_collection_ids(db, principal, ids)` — narrow a
  candidate list to what the principal may browse (used by the grant-scoped `/workspaces` + `/collections`
  reads and by `/search`; `/search` 403s when none survive).
- `readable_collection_scope(db, principal)` — the *whole* set of collection ids the principal may read, or
  `None` when unrestricted (master/admin). Unlike `readable_collection_ids` (which filters a candidate list),
  this returns the full set — for the global cross-collection views (ingestion queue list/stats, index
  status, the `ingestion-queue` WS) that have no candidate list. Document-only grants are excluded (these are
  collection-grained views, like the documents listing).
- `filter_workspace_tree(db, principal, tree)` — prunes the MCP `list_workspaces` tree to readable nodes.

Three FastAPI deps in `auth.py`, all built on `resolve_bearer` (JWT-or-key): `require_master` (403 unless
master-equivalent), `require_auth` (any valid credential; records `last_used_at`; rejects a must-change
session), and `require_operator` (a user session incl. must-change — only the self-service `/auth` routes).
Patterns:
- **Management writes** → `require_master` (router-level for `tags`, `config`, `graph`, **users**; per-route
  on `workspaces`/`collections` **writes** and the `jobs` bulk **retry-failed**). Creating/updating/deleting
  anything, and all user/key/grant management, is **admin-only**.
- **Data-plane + grant-scoped reads** → `require_auth` then `await permissions.authorize_*` /
  `readable_*` (documents, search, the ws bridge, the `workspaces`/`collections` **list/get**, and the
  **ingestion-queue list/stats + index status** via `readable_collection_scope`). A non-admin sees only what
  their grants reach; master/admin see everything. The global `ingestion-queue` WS is likewise grant-filtered
  per event (fail-closed on a malformed/collection-less event). The console exposes the queue + indexing
  pages to non-admins as read-only views (retry actions stay admin-only in the UI).

## Users management API (`api/routers/users.py`, `api/services/users.py` — master-only)
CRUD users (create/list/get/update/delete), activate/deactivate (`is_active`), mint/rotate/revoke the user's
one key (`POST|DELETE /users/{id}/key`; mint returns the raw key once, and **replaces** any existing key —
that's rotation), and grant CRUD (`GET|POST /users/{id}/permissions`, `DELETE …/{grant_id}`, delegating to
the permissions service). Key minting lives in `users.py` — the sole place keys are created.
Creating a user generates a **random one-time password** (returned once, like a minted key) with
`must_change_password=True`; `POST /users/{id}/reset-password` (master-only) regenerates it and bumps the
session epoch. `username` (`UNIQUE`) + `is_admin` are set here.

## Console login API (`api/routers/auth.py`, `api/services/session.py` — the login plane)
`POST /auth/login` (public) verifies username + password (coarse `401`; timing-equalized dummy compare on a
miss) and returns a signed session JWT. `POST /auth/change-password` + `GET /auth/me` use `require_operator`
(a user session, must-change allowed). A must-change session can reach **nothing else** until the password is
set. Password hashing + temp-password generation live in `session.py` (bcrypt `rounds=12`, same as keys).

## MCP
The MCP transport authenticates the caller's key (master or active user) in `api/services/mcp/middleware.py`
and binds the resolved `Principal` for the request (`api/services/mcp/context.py`); each tool enforces that
principal's grants (`api/services/mcp/tools.py`) and returns a tool error (403) when denied. **Exception:**
`ingest_document` references an arbitrary container-local path (`documents.ingest_local_path`), so it is
**master-only** — a scoped write grant must not become an arbitrary server-file read / cross-tenant copy.
Over-limit → 429 (per-key token bucket). See [`mcp.md`](mcp.md).

## Hard rules / gotchas
- **Never return or log `key_hash`, `password_hash`, a raw key, or a raw password.** Hashing is fixed: bcrypt,
  `rounds=12`; the prefix is only a candidate filter — never authenticate on prefix alone. `_serialize_user`
  stays hash-free. Auth errors are coarse (login/missing/invalid → 401; inactive/unauthorized → 403; the
  must-change gate → 403) — don't leak which part failed.
- **JWTs are console-only.** `resolve_bearer` (REST/WS) accepts them; `authenticate_api_key` (MCP) does not,
  so a session token can't drive MCP. Sign with `session._signing_key()` and always `decode` with
  `algorithms=["HS256"]` + `require=["exp"]` (block `alg:none`/confusion, no non-expiring tokens).
- **One authority**: resource decisions go through `permissions.authorize_*` — don't add ad-hoc scope checks in
  routers/tools. There is no `Principal.can_access` anymore.
- `record_key_use` now runs in `require_auth` and the MCP middleware, so `last_used_at` is live (was a known gap).
- Config secrets are masked separately ([`configuration.md`](configuration.md)) — same "never expose secrets"
  rule, different code path.
