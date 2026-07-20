---
paths:
  - "api/db.py"
  - "api/tables/**"
  - "api/alembic/**"
  - "worker/db.py"
---

# Metadata DB — ORM only

The metadata DB (**workspaces, collections, documents, tags, api_keys, job_records**) goes through the
**SQLAlchemy 2.0 async ORM**. The `chunks` table is **not** here — it's the vector store's, raw asyncpg,
see [`vector-db.md`](vector-db.md).

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
- Hierarchy is `workspaces → collections → (documents, api_keys, job_records)` with FK `CASCADE`.
- `expire_on_commit=False`, `autoflush=False` — flush/commit intentionally explicit.
