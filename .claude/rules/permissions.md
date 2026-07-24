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
**scope-restricted** (a non-admin user: **permissions scope them *down*** over the `workspace → collection →
document` hierarchy — with none they see and write everything, and each permission narrows what they reach).
The auth core is `api/services/auth.py` (+ `session.py` for JWTs); the single authorization authority is
`api/services/permissions.py`. **MCP stays API-key-only — JWTs never reach it.**

## Credentials
- **Master key** — the one required secret `MASTER_API_KEY` (`api/settings.py`; app won't start without it).
  Master-equivalent (bypasses every check). Matched with `secrets.compare_digest` (constant-time).
- **Console session (JWT)** — from `POST /auth/login` (username + bcrypt password). A short-lived **HS256**
  token signed with a key **derived from `master_api_key`** (`api/services/session.py`; no separate secret —
  rotating the master invalidates every session). Sent as `Authorization: Bearer`. An **admin** user
  (`users.is_admin`) resolves master-equivalent; a **non-admin** resolves scope-restricted. `resolve_bearer`
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

## Permissions (`permissions` table) — a scope-*down* model
A permission gives one user a `level` (`read` | `write`) on one resource (`workspace` | `collection` |
`document`). Permissions **restrict**; they don't grant access from nothing:
- **No permissions at all → unrestricted**: the user reads *and writes* every workspace, collection, and
  document (open default). Master and admin users are always unrestricted.
- **Any permission scopes the user** to the workspace(s) it *touches* — a collection/document permission
  scopes them to that resource's workspace; other workspaces disappear.
- **Per-level narrowing** inside a visible workspace: a workspace-only permission leaves every collection
  visible; a collection permission narrows to just the permitted collections. A **document** permission is
  *direct-access* to that one document (get status / download / delete it) — it scopes the user into the
  document's workspace but does **not** open its collection for browsing or search, so it can't leak the
  collection's siblings through the collection-grained readers (list / search / jobs / indexing).
- **Read vs write**: a user with **no permissions writes everything**; a scoped user writes only where a
  *write* permission covers the resource (on it or an ancestor) — everything else in their scope is
  view-only. `read` = search / list / download; `write` = ingest / delete (and read). `write` implies `read`.

`resource_id` is a plain string (no FK — polymorphic across three tables, like `job_records`); a permission
left dangling by a deleted resource is harmless (uuids are never reissued).

### Capabilities — grantable privileges not tied to a resource
Some privileges have no parent resource to hold a write grant — creating a top-level **workspace** is the
one so far. These are **capability grants**: a `permissions` row with `resource_type="capability"` and a
known `resource_id` (`create_workspace`; see `_CAPABILITIES` / `_CAPABILITY_LABELS`). They're **ignored by
data-scope resolution** (a capability never scopes a user's read/write), checked with `has_capability` /
`authorize_workspace_creation`, and granted through the same `POST /users/{id}/permissions` + Permissions
editor. A no-permission user is unrestricted for *data* but still needs the capability to create workspaces.

## Enforcing access — `api/services/permissions.py` is the only authority
Routers and MCP tools call it and let it raise `403`; **never** re-implement the check inline. A restricted
user's permissions resolve once to a `_Scope` (permissions + parent links pre-fetched) that answers
readability/writability per level:
- `authorize_workspace(db, principal, ws_id, need)` / `authorize_collection(...)` /
  `authorize_document(...)` — master/admin **and users with no permissions** pass; a scoped user passes iff the
  resource is in scope (visible) and, for `write`, no `read`-level permission on it or an ancestor caps it.
- `readable_workspace_ids(db, principal, ids)` / `readable_collection_ids(db, principal, ids)` — narrow a
  candidate list to what the principal may browse (master + no-permission users keep all; a scoped user keeps
  only in-scope ids). Used by the `/workspaces` + `/collections` reads and by `/search` (403s when none survive).
- `readable_collection_scope(db, principal)` — the *whole* set of readable collection ids, or `None` when
  unrestricted (master, admin, or **no permissions**). For the global cross-collection views (ingestion queue
  list/stats, index status, the `ingestion-queue` WS) that have no candidate list to filter.
- `filter_workspace_tree(db, principal, tree)` — prunes a workspace tree to readable nodes (the whole tree for
  unrestricted; scoped otherwise).

### Access policies — composable authorization + existence (`api/services/access.py`)
Per-resource authorization in the `workspaces` / `collections` / `documents` / `indexing` routers goes through
**access policies**: small objects implementing the `AccessPolicy` Protocol (`async apply(db, principal)`,
which **raises** `HTTPException` to deny and returns `None` to allow). Concrete policies wrap the authority —
`AuthorizeWorkspace` / `AuthorizeCollection` / `AuthorizeDocument` (403, via `permissions.authorize_*`) — and
the domain existence checks — `CollectionInWorkspace` (404, via `collections.require_collection`). A route
composes what it needs with **`CompositePolicy(*policies)`**, which applies them **in order** and lets the
first denial propagate:

```python
await CompositePolicy(
    AuthorizeDocument(doc_id, "write"),     # 403 if the caller may not write it
    CollectionInWorkspace(ws_id, col_id),   # 404 if the URL's collection path is wrong
).apply(db, principal)
```

**Order is a security property**: authorization policies precede existence policies, so a scoped caller who
may not reach a resource gets a uniform 403 whether or not it exists (the 404 can never be an existence
oracle). This is *why* `apply` raises rather than returning a bool (as in the canonical pattern) — the
policies surface **different** status codes (403 vs 404) and which fires first is what closes the oracle.
Policies hold **no scope logic of their own** — they only compose `permissions.authorize_*` with the existence
checks. Single-check routes apply one policy (`await AuthorizeCollection(...).apply(...)`); the multi-check
`documents` (list / status / delete / reprocess) and `indexing` routes use `CompositePolicy`. Two concerns stay
direct `permissions.*` calls because they gate a *set* or a capability, not one resource: list-filtering
(`readable_*` / `writable_*` / `filter_workspace_tree`) and workspace creation (`authorize_workspace_creation`).
And two routes authorize inside their service: **raw-download** (`resolve_document_download`, authorize-first)
and **upload** — the router confirms the collection exists (`resolve_collection`) *before* `ingest` authorizes,
so it stays existence-first, a minor non-enumerable residual (collection ids are random) left as-is because
`ingest` keeps its write-authorization in-service by design.

Three FastAPI deps in `auth.py`, all built on `resolve_bearer` (JWT-or-key): `require_master` (403 unless
master-equivalent), `require_auth` (any valid credential; records `last_used_at`; rejects a must-change
session), and `require_operator` (a user session incl. must-change — only the self-service `/auth` routes).
Patterns:
- **Management writes** → `require_master` (router-level for `tags`, `config`, `graph`, **users**; per-route
  on the `jobs` bulk **retry-failed**). User/key/grant + tag management stays admin-only. **Workspace &
  collection writes are scope-permissioned, not admin-only:** create a **collection** with `require_auth` +
  workspace **write**, and **edit/delete** one with `require_auth` + **write** on the collection or an
  ancestor (`authorize_collection(…, "write")`); create a **workspace** with `require_auth` + the
  **`create_workspace` capability** (`authorize_workspace_creation`, scoped creator auto-granted write via
  `grant_creator_access`), and **edit/delete** one with `require_auth` + workspace **write**
  (`authorize_workspace(…, "write")`). `write` implies `read`, so a user only edits what their grants let
  them see. The `workspaces`/`collections` list+get report **`can_write`** (via `writable_workspace_ids` /
  `writable_collection_ids`) so the console shows edit/delete/upload only where they'd succeed.
- **Data-plane + scope-restricted reads** → `require_auth` then `await permissions.authorize_*` /
  `readable_*` (documents, search, the ws bridge, the `workspaces`/`collections` **list/get**, and the
  **ingestion-queue list/stats + index status** via `readable_collection_scope`). A non-admin with no
  permissions sees everything; a scoped one sees only what's in scope; master/admin see everything. The global
  `ingestion-queue` WS is scope-filtered per event (fail-closed on a malformed/collection-less event). The
  console exposes the queue + indexing pages to non-admins as read-only views (retry actions stay admin-only
  in the UI).

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
The console client treats a **dead credential as sign-out**: any `401` (expired / invalid / reset session)
and the deactivated-user `403` ("User is inactive") clear the stored credentials and return the app to the
login screen (`ui/src/api/client.ts::raiseForStatus` → the auth provider's `logout`).

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
