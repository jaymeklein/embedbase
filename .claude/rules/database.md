---
paths:
  - "api/db.py"
  - "api/tables/**"
  - "api/alembic/**"
  - "worker/db.py"
---

# Metadata DB — ORM only

The metadata DB (**workspaces, collections, documents, tags, users, api_keys, permissions, job_records**) goes
through the **SQLAlchemy 2.0 async ORM**. The `chunks` table is **not** here — it's the vector store's, raw
asyncpg, see [`vector-db.md`](vector-db.md).

## The rules
- **Engine + session factory live in `api/db.py`** (`engine`, `AsyncSessionLocal`, `init_db`). Table objects
  live in `api/tables/` and are re-exported from `api.db`, so `from api.db import documents` stays valid.
- **Build queries with `select(...)` / `insert` / `update` / `delete` and the table objects on the injected
  `AsyncSession`.** **Do not hand-write raw SQL against the metadata DB.**
- **Every schema change is an Alembic migration** in `api/alembic/versions/`. `init_db()` runs
  `alembic upgrade head` at startup (safe on every boot) from a thread executor.
- Services own the queries and commit explicitly; routers stay thin (see [`api.md`](api.md)). The request
  gets its `AsyncSession` via `Depends(get_db)` — unhandled exceptions auto-rollback.

## Good to know
- URL comes from `DATABASE_URL` (e.g. `postgresql+psycopg://…`); the dev fallback is
  `sqlite+aiosqlite:///…`. SQLite-only pragmas (WAL, `foreign_keys=ON`) are set on the sync connection at
  connect time — don't apply them to Postgres.
- Hierarchy is `workspaces → collections → (documents, job_records)` with FK `CASCADE`. **Users** own their
  data separately: `users → (api_keys [UNIQUE(user_id)], permissions)` with FK `CASCADE`. The `permissions`
  `resource_id` is a plain string (polymorphic, no FK — like `job_records`).
- **Users login (migration 0009):** `users` also carries `username` (`UNIQUE` via the `users_username_key`
  index — declared as an `Index(..., unique=True)`, not a `UniqueConstraint`, so the migration's
  `create_index` and the table metadata agree for the parity test), `password_hash`, `must_change_password`,
  `password_changed_at` (the JWT session epoch), and `is_admin`. All have server defaults so 0009 adds them
  one-shot; existing rows backfill `username = email`. Never `select`-project or serialize `password_hash`.
- **Per-user MCP rate limit (migration 0010):** `users.rate_limit_rpm` (INTEGER NOT NULL, server_default `0`) —
  `0` = inherit the global `mcp.rate_limit_rpm`, a positive value caps that user's MCP key. Read into the
  `Principal` at API-key auth and pushed into the per-key token bucket (see [`mcp.md`](mcp.md)).
- **Optional email (migration 0011):** `users.email` is nullable — an account is identified by `username`, so
  it may have none. `UNIQUE(email)` stays (SQL treats NULLs as distinct, so multiple no-email rows are fine);
  the `OptionalEmail` schema coerces a blank address to `None` so it's never stored as `''`. 0011 uses
  `op.batch_alter_table` so the nullable change also applies on SQLite (dev/tests can't ALTER-COLUMN in place).
- `expire_on_commit=False`, `autoflush=False` — flush/commit intentionally explicit.
