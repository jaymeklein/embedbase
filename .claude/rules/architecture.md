---
paths:
  - "api/**"
  - "worker/**"
---

# Architecture conventions — so "follow the pattern" is concrete

The principles behind these are in [`code-standards.md`](code-standards.md). This file is the
"how, in this repo."

## Adapters — the extension seam
`api/adapters/{embeddings,parsers,reranker,vector_store}` (also `tagging`, `llm_chat`):

- A `runtime_checkable` **`Protocol` in `base.py`**, **one provider per file**, resolved by a
  **`get_*(config)` factory** that dispatches on `config.provider` (or file type) with **lazy imports
  inside each branch** and **raises `ValueError` on an unknown provider**.
- A disabled optional stage's factory returns **`None`** so callers skip it with `is not None`
  (see `get_reranker`).
- **Adding a backend = a new file under `api/adapters/<kind>/` + one branch in that kind's factory.**
  Callers depend on the Protocol, never on a concrete class. Do not edit callers to add a backend.
- Keep the Protocols minimal (ISP). Optional capabilities stay *off* the Protocol — e.g. a parser's
  `on_progress(current, total)` is detected by signature inspection in the worker, not declared on
  `ParserAdapter`.

## Graceful degradation — a hard rule
Any stage that lacks a model, times out, or errors **falls back to prior behaviour and never turns a
search / ingest into a 500.** Concretely: a missing reranker → RRF-only ranking; adjacency expansion is
best-effort; `/healthz` surfaces the degrade. **A new stage that can 500 the request is a bug.**

## Layering — one direction only
`router → service → (adapter | db)`. Never the reverse; adapters/db never import services/routers.
- **Routers** (`api/routers/`) are *routing-only*: path + `Depends(...)`, an authorization check, then a
  single delegating call to a service. No business logic, no raw SQL, no schema declarations there.
- **Services** (`api/services/`) own business logic + persistence; they raise `HTTPException` for domain errors.
- Details of the HTTP layer: [`api.md`](api.md).

## Where the specifics live
- **Config** (pydantic models, secrets, hot-reload): [`configuration.md`](configuration.md).
- **Metadata DB** (ORM-only, tables, Alembic): [`database.md`](database.md).
- **Vector store** (the one raw-asyncpg exception): [`vector-db.md`](vector-db.md).
- **Tests / fakes / DI-for-testability**: [`testing.md`](testing.md).
