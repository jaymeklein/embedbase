# CLAUDE.md — working agreement for **embedbase**

Binding for every change, Claude and humans alike. The bar is not "make it work."
The bar is **make it work, simply, without repeating yourself — and prove it.**

This file exists because standards get skipped when they're implicit. They are not
implicit here.

---

## The loop — every change, no exceptions

1. **Understand before writing.** Search the repo for an existing pattern, helper,
   constant, config field, adapter, or test double that already does what you need,
   and reuse or extend it. About to add a `class`/`def`/constant? `grep` its name
   and its siblings **first** — if a sibling exists, follow it; if a copy exists,
   you're creating duplication, stop.
2. **Implement to the standards below** — DRY · KISS · YAGNI · SOLID.
3. **Pass the gate** — ruff + mypy + pytest all green (commands at the bottom).
   A change that fails the gate is not done.
4. **Run `/code-review`.** **Mandatory at the end of every implementation**, before
   you call the work finished or offer to commit. Address every finding; re-review
   if the fixes are non-trivial. Do not report a task complete without it.

Skipping step 1 or step 4 is the exact failure mode this document prevents.

---

## Code standards

### DRY — one fact, one place
- **Search for an existing implementation before writing a new one**, and reuse it.
  Duplication discovered later is a defect, not a style nit.
- **Shared test doubles live in [`tests/unit/fakes.py`](tests/unit/fakes.py) and
  [`tests/unit/fake_redis.py`](tests/unit/fake_redis.py)** — import them
  (`from tests.unit.fakes import FakeStore, FakeEmbedder`). **Never** re-declare
  `FakeStore` / `FakeEmbedder` / `FakeRedis` inside a test module. A new reusable
  double goes in those modules, not inline.
- Shared domain logic belongs in a service/adapter/module — never copy-pasted across
  routers, tasks, or tests.
- A literal used in more than one place becomes a named constant (see
  [`api/constants.py`](api/constants.py)).
- **Fixing a DRY violation means finding *every* copy**, not only the one that was
  pointed out.

### KISS — the simplest thing that works
- Prefer a function to a class, a plain call to a framework, a straight line to a
  clever one. Optimise for the next reader.
- No premature abstraction: the **second** concrete case justifies an interface, the
  first does not.
- If a change needs a paragraph to explain why it's clever, it's probably wrong.

### YAGNI — build what's needed now
- No config knobs, parameters, hooks, or "for later" branches without a **current**
  caller. The roadmaps in [`plans/`](plans/) and [`docs/`](docs/) are staged on
  purpose — don't pull a future phase forward.

### SOLID — where it earns its keep
- **SRP** — one reason to change per unit. Parsing ≠ chunking ≠ embedding ≠ storing;
  keep them separate, mirroring `api/services/` and `api/adapters/`.
- **OCP** — extend by adding, not by editing callers. A new embedding / reranker /
  parser backend is **a new file under `api/adapters/<kind>/` plus one branch in
  that kind's `get_*()` factory** — callers depend on the factory, never on a
  concrete class.
- **LSP** — a new adapter honours its Protocol's full contract, including graceful
  degradation (below).
- **ISP** — keep the Protocols in [`api/adapters/base.py`](api/adapters/base.py)
  minimal. Optional capabilities stay *off* the Protocol (see the `on_progress` note
  on `ParserAdapter`) so implementations that lack them still conform.
- **DIP** — depend on the `Protocol`, inject the dependency. `_run_ingestion` takes
  `embedder` / `vector_store` / `storage` as parameters — that's *why* it is testable
  with the fakes above. Follow that shape.

---

## Architecture conventions (so "follow the pattern" is concrete)

- **Adapters** (`api/adapters/{embeddings,parsers,reranker,vector_store}`):
  a `runtime_checkable` `Protocol` in `base.py`, one provider per file, resolved by a
  `get_*(config)` factory that dispatches on `config.provider` with **lazy imports
  inside each branch** and **raises `ValueError` on an unknown provider**. A disabled
  optional stage returns `None` so callers skip it with `is not None`
  (see [`get_reranker`](api/adapters/reranker/__init__.py)).
- **Graceful degradation is a hard rule.** Any stage that lacks a model, times out,
  or errors **falls back to prior behaviour and never turns a search/ingest into a
  500.** A new stage that can 500 the request is a bug.
- **Config** is pydantic models in [`api/models/config.py`](api/models/config.py).
  `config.yaml` and `.env` are **gitignored — never commit secrets.** A new secret
  field must be added to `SECRET_PATHS` in
  [`api/services/config_service.py`](api/services/config_service.py) so it is masked
  on GET and preserved on PUT.
- **Database access — ORM only for the metadata DB.** The metadata DB (workspaces,
  collections, documents, tags, api_keys, job_records) goes through the **SQLAlchemy
  2.0 async ORM**: engine + session in [`api/db.py`](api/db.py), table objects in
  [`api/tables/`](api/tables/), every schema change an Alembic migration in
  `api/alembic/versions/`. **Do not hand-write raw SQL against the metadata DB** —
  build queries with `select(...)` and the table objects on the injected
  `AsyncSession`. The **one** sanctioned exception is the pgvector vector store
  ([`api/adapters/vector_store/pgvector.py`](api/adapters/vector_store/pgvector.py)):
  the `chunks` table has no ORM model and is accessed with raw, **parameterised**
  asyncpg, because it needs pgvector (`<=>`) and ParadeDB `pg_search` (`|||`,
  `pdb.score`) operators the ORM can't express, on a loop-bound pool the request
  `AsyncSession` can't share. New `chunks` queries follow that adapter's raw-asyncpg
  sibling pattern (simple ones inline, complex ones as module-level `_*_SQL` constants).
- **Tests** live in `tests/{unit,integration,smoke}`. Unit tests touch no network,
  DB, or Redis — use the shared fakes. **Every behavioural change ships with a test.**

---

## The gate — run before every "done"

The toolchain lives in the WSL virtualenv. From the repo root:

```bash
.venv/bin/ruff check api/ worker/ tests/
.venv/bin/mypy api/ worker/ --ignore-missing-imports --explicit-package-bases
.venv/bin/python -m pytest tests/unit tests/integration -q
```

All three green, **then `/code-review`.** This mirrors CI
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): ruff rules `E,F,I,UP,B,SIM`
(line-length 100, target py312), mypy on `api/ worker/`, the unit and integration
suites, and the docker build smoke test. Ruff now lints `tests/` too — test code is
held to the same standard as shipping code.

---

## Commits (only when explicitly asked)

- **Conventional Commits**: `type(scope): summary` (`feat`, `fix`, `refactor`,
  `docs`, `test`, `chore`, …). Same format for PR titles.
- **No AI attribution** — commit under the user's identity. No `Co-Authored-By:
  Claude`, no "Generated with" trailer.
- Don't commit or push unless asked; branch off `main` first when needed.

## Libraries & docs

Use the **Context7 MCP** for any library / framework / SDK / API / CLI question
(syntax, config, migrations, debugging) *before* answering from memory — training
data lags real releases.
