---
paths:
  - "api/adapters/vector_store/**"
---

# Vector store — the one sanctioned raw-asyncpg path

`api/adapters/vector_store/pgvector.py`. The **`chunks` table has no ORM model by design** and is accessed
with **raw, parameterised asyncpg** — the single documented exception to the ORM-only rule
([`database.md`](database.md)). Everything else about the adapter pattern still applies
([`architecture.md`](architecture.md)): it's resolved by `get_vector_store(config)`.

## Why raw asyncpg (don't "fix" this to ORM)
- It needs operators the ORM can't express: **pgvector `<=>`** (cosine distance) and **ParadeDB `pg_search`**
  (`|||` match-any-term, `pdb.score(id)` = Okapi BM25).
- asyncpg is async-only but the `VectorStoreAdapter` Protocol is **sync** and called from both the worker
  (no loop) and the API (inside a loop). Each adapter owns a **background thread + persistent event loop**
  (`_AsyncRunner`); sync methods submit coroutines to it. asyncpg pools are **loop-bound**, so the pool
  binds to that one loop — a request's `AsyncSession` can't share it.

## Conventions for new `chunks` queries
- **Always parameterised** (`$1, $2, …`) — never interpolate values. The `{filter}` placeholder is the
  only composed fragment (a metadata AND-chain from `_metadata_filter_sql`, pushed into the WHERE for
  pre-filter pushdown); build it the same way, with bound params.
- **Simple queries inline; complex ones as module-level `_*_SQL` constants** (`_UPSERT_SQL`, `_SEARCH_SQL`,
  `_BM25_SQL`, `_HYBRID_SQL`, `_LIST_SQL`). Follow that sibling pattern.
- **Hybrid search is one round-trip**: `_HYBRID_SQL` fuses semantic + BM25 via **Reciprocal Rank Fusion in
  SQL** (CTE + `RANK()` + `UNION ALL`), weighted by `alpha`. Don't move fusion back into Python. It surfaces
  `from_bm25` (so the caller can degrade to semantic-only) and `vscore` (real cosine, kept for that case).
- **BM25 / lexical is not a pipeline stage** — it's the stored `chunks.text_tsv` / `pg_search` index,
  maintained by the upsert. `index_document` / `index_collection` are no-ops kept for API compatibility.
- Each collection gets a **lazily-built partial HNSW index** (`WHERE collection_id = …`) once it crosses
  `index_min_rows`, built with `CREATE INDEX CONCURRENTLY` so it never locks the table.
- Chunk ids are deterministic (`make_chunk_id`), so upsert (`ON CONFLICT (id)`) is re-runnable — the basis
  for ingestion resume ([`ingestion.md`](ingestion.md)).
