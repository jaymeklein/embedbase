---
paths:
  - "tests/**"
---

# Tests

Tests live in `tests/{unit,integration,smoke}`. **Every behavioural change ships with a test.**
`tests/` is linted and type-checked to the same bar as shipping code (the gate in `CLAUDE.md`).

## Unit tests touch nothing external
No network, no DB, no Redis. Use the **shared fakes** — never spin up a real model or pgvector:
- `tests/unit/fakes.py` — `FakeEmbedder` (deterministic 3-dim vectors) and `FakeStore` (in-memory
  vector store that records `upserts` and answers `document_chunk_ids_at_model`, the resume set).
- `tests/unit/fake_redis.py` — `FakeRedis`.

## The DRY rule for doubles (do not violate)
- **Import** the shared doubles: `from tests.unit.fakes import FakeStore, FakeEmbedder`.
- **Never re-declare** `FakeStore` / `FakeEmbedder` / `FakeRedis` inside a test module.
- A **new reusable** double goes in `fakes.py` / `fake_redis.py`, not inline — one contract, one place,
  so every test sees the same behaviour and a change is made once.

## Why the pipeline is testable
`_run_ingestion` (and `_embed_and_store`) take `embedder` / `vector_store` / `storage` / `redis_client`
as injected params — that's the DIP shape ([`code-standards.md`](code-standards.md)) that lets the whole
ingestion flow run against the fakes with no infrastructure. When you add a stage, keep its deps injected
so a unit test can substitute a fake. Pipeline details: [`ingestion.md`](ingestion.md).

Run: `.venv/bin/python -m pytest tests/unit tests/integration -q`.
