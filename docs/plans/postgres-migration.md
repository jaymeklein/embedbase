# Postgres Consolidation Plan

Move EmbedBase's **durable storage and lexical indexing** onto a single Postgres
instance, retiring the SQLite metadata DB, the Chroma vector store, and the Redis
BM25 corpus blob. Redis **stays** — as the Celery broker, realtime pub/sub, and
ingestion heartbeats (the things it's actually good at).

Guiding rule (same as the retrieval plan): each phase ships on its own and the
app keeps working if later phases never land. Backend selection is already
config-driven, so every phase is a flip-and-verify, not a rewrite.

| # | Phase | Solves | Status |
|---|-------|--------|--------|
| 0 | Land pending work on `main` | clean base to branch from | `[ ]` |
| 1 | Relational store → Postgres | "storage": metadata off SQLite | `[ ]` |
| 2 | Vectors → pgvector (already coded) | "storage": vectors off Chroma | `[ ]` |
| 3 | Lexical/BM25 → Postgres FTS | "indexing": kill the Redis corpus blob | `[ ]` |
| 4 | Pre-filter pushdown + single round-trip hybrid | "query times" | `[ ]` |

Messaging (Celery broker + result backend) **stays in Redis** — confirmed with
the user; it's not in scope for this migration.

---

## What already exists (≈40% done)

- **`PgvectorAdapter`** (`api/adapters/vector_store/pgvector.py`, 360 lines) — full
  `VectorStoreAdapter`: upsert, cosine search (`1 - (embedding <=> $1)`),
  `iter_document_chunks`, `set_document_tags`, delete document/collection. Builds
  an **HNSW** index (`CREATE INDEX CONCURRENTLY`, `vector_cosine_ops`), bootstraps
  the `vector` extension and a `chunks(id, collection_id, text, metadata,
  embedding vector(dim))` table.
- **Backend registry** (`api/adapters/vector_store/backends.py`) — `VECTOR_STORE=pgvector`
  env selects it. `chroma` / `qdrant` also registered.
- **`docker-compose.postgres.yml`** — `pgvector/pgvector:0.7.0-pg16`, api+worker
  already pointed at it with `VECTOR_STORE=pgvector`.

## What is NOT on Postgres (the real gaps)

1. **Relational metadata is still SQLite.** `api/db.py:57` (`sqlite+aiosqlite:///`)
   and `worker/db.py:49` (`sqlite:///`) hardcode SQLite. `docker-compose.postgres.yml`
   overrides only the vector store — it never repoints the relational DB. So
   workspaces / collections / documents / job_records / tags stay in
   `/store/embedbase.db` even in "postgres mode."
2. **BM25 lexical index is the Redis blob** (`bm25:{col}:corpus`) — the 6.4 MB
   whole-collection JSON, rewritten on every ingest, rebuilt in-process per version.
   Text is duplicated (already in Chroma/pgvector `chunks.text`).
3. **Portability blockers**: SQLite-only upserts —
   `api/services/tags.py:18` and `worker/tasks.py:16`
   (`from sqlalchemy.dialects.sqlite import insert`). Break on Postgres.

---

## Phase 0 — Land the pending work first `[ ]`

The working tree has unmerged work (score fix, Ingestion Queue, resume,
heartbeat/progress-timeout). Commit → PR → merge to `main` so the migration
branches from a known-good base. Not part of the migration, but a prerequisite.

---

## Phase 1 — Relational store on Postgres `[ ]`

**Make the relational engine URL configurable** (today it's a hardcoded SQLite path):

- `api/db.py` / `worker/db.py`: read `DATABASE_URL` (default
  `sqlite+aiosqlite:///…` / `sqlite:///…` for dev back-compat). Add `asyncpg`
  (async) + `psycopg`/`psycopg2` (worker sync) drivers.
- Guard `_set_sqlite_pragmas` behind an `if url.startswith("sqlite")` — no-op on PG.
- **Fix the two upsert sites** to be dialect-aware: a small helper that picks
  `postgresql.insert` vs `sqlite.insert` by bind dialect (both expose
  `on_conflict_do_nothing` / `on_conflict_do_update`). Touches `tags.py`,
  `worker/tasks.py`.
- **Alembic audit**: run migrations against PG; replace any SQLite-only column
  types/defaults. Most SQLAlchemy core migrations port cleanly.
- `docker-compose.postgres.yml`: add `DATABASE_URL` (built from the existing
  `POSTGRES_*`) to api + worker.

**Verify**: metadata CRUD (create workspace/collection, ingest, tag, delete)
against Postgres; existing unit tests green on both SQLite and PG.

---

## Phase 2 — Vectors on pgvector `[ ]`

Code is done; this is consolidation + verification.

- Point `PgvectorAdapter` at the **same** Postgres instance as Phase 1 (one server;
  enables future joins chunks↔documents and one backup target).
- `embeddinggemma` = 768-dim → `chunks.embedding vector(768)`. Confirm dimensions
  flow from embedder config (they do, via `embedding_dimensions` in the registry).
- Re-ingest the corpus (deterministic `make_chunk_id` makes it idempotent/resumable).
- Verify search/upsert/delete parity with Chroma; confirm HNSW index builds past
  `index_min_rows`.

---

## Phase 3 — Lexical/BM25 in Postgres FTS `[ ]` (the "indexing" win)

Retire the Redis corpus blob; do keyword search **in the DB, incrementally**.

- Add a generated `tsvector` column + **GIN index** to `chunks` (populated on
  upsert). `chunks.text` already lives there — no duplication.
- Replace the Redis path in `api/services/search.py` `_get_bm25_scores`:
  `websearch_to_tsquery` + `ts_rank_cd`, scoped by `collection_id`, keyed by
  `chunk_id`. RRF fusion (`_reciprocal_rank_fusion`) is rank-based, so it's
  unaffected by the score-source swap.
- **Delete**: `_reindex_document_bm25` (whole-blob rewrite), the in-process
  `_bm25_cache`, `bm25:{col}:corpus` / `:version` writes, and `api/models/redis.py`
  `Corpus`/`CorpusConfig`. `api/services/indexing.py`'s "indexed = chunks in
  corpus" definition moves to "rows in `chunks`."

This kills, in one move: O(collection) rewrite per ingest, per-process rebuild,
the cold-start blob fetch, the per-process cache (so it scales to N API replicas),
and the text duplication.

> **Honest caveat:** Postgres `ts_rank_cd` is **not** true Okapi BM25 (no IDF
> saturation / avgdl the same way). For this corpus, fused under RRF, the ranking
> difference is negligible. If exact BM25 is required later, the **ParadeDB
> `pg_search`** extension provides real BM25 inside Postgres — a drop-in for the
> `ts_rank` query, same table. Start with native FTS; upgrade only if measured.

---

## Phase 4 — Query-time wins `[ ]`

Be honest about what Postgres does and doesn't do for **query latency**:

**It does NOT speed up ANN.** pgvector HNSW ≈ Chroma HNSW — same algorithm, both
in-memory graph. Raw vector-search latency is a wash. And a warm in-process
`BM25Okapi` (today) is *faster* per-query than a PG FTS round-trip, since it's
numpy scoring already in RAM. So "move to PG" is not, by itself, a query-time win.

**Where the real query-time wins are** (all enabled by consolidation, not free):

1. **Pre-filter pushdown.** Today `apply_filters` runs *after* the vector search
   (`api/services/search.py`), so restrictive `tags`/`filename` filters over-fetch
   and can under-deliver (already flagged in `retrieval-upgrade-plan.md`). In
   Postgres, fold the filter into the `WHERE` of the vector/FTS query — fewer rows
   scanned, no over-fetch, correct top-k. This is the biggest latency+correctness
   lever.
2. **Single round-trip hybrid.** Today hybrid = embed → Chroma call (network) +
   Redis/BM25 (in-proc) → RRF in Python. In Postgres it's **one** SQL statement:
   vector `ORDER BY embedding <=> $1`, `ts_rank_cd`, and the filter `WHERE`,
   combined. One round-trip, less app-side glue, better tail latency.
3. **No BM25 cold-start cliff.** The in-process cache is fast *warm*, but every
   restart / reindex pays a full blob fetch + tokenize + `BM25Okapi` rebuild
   (linear in corpus). PG FTS is incremental — no rebuild, no cold p99 spike, and
   it's replica-safe (no per-process cache to warm N times).

**Honest trade-off:** steady-state warm single-node BM25 may be marginally faster
than PG FTS; you're trading a small warm-path cost for pre-filtering, one
round-trip, no cold-start cliff, and horizontal scalability. Net win once you
filter, restart, or run >1 replica — not before.

- Depends on Phase 1–3 (needs metadata + vectors + `tsvector` co-located in PG).
- Overlaps `retrieval-upgrade-plan.md`'s "filtering is post-ranking" note — this
  is where that gets fixed.

---

## Data migration

- **Dev (now):** one test collection → **re-ingest**, don't build a migration tool.
  Idempotent via deterministic chunk ids.
- **Prod (if/when):** `pgloader` for the relational tables (SQLite→PG in one pass);
  vectors either copied or re-embedded (re-embed is simplest and idempotent).
  Lexical needs nothing — the `tsvector` populates from `chunks.text` on load.

## Risks / gotchas

- Alembic may hide SQLite-only constructs — audit before trusting Phase 1.
- Two connection layers hit the same DB (SQLAlchemy async engine + asyncpg pool
  inside `PgvectorAdapter`) — fine, but size both pools with one server in mind.
- Keep the SQLite path working (default) through all phases so dev/CI needs no PG
  until deploy — every phase stays reversible by config.

## Net result

- **Storage**: SQLite + Chroma + Redis-blob → **one Postgres** (metadata + vectors
  + lexical), one backup target, joinable.
- **Indexing**: no blob rewrite, no in-process rebuild, incremental, replica-safe.
- **Query**: pre-filter pushdown + single round-trip hybrid + no BM25 cold-start
  cliff (ANN latency itself is unchanged — that's honest).
- **Messaging**: unchanged — Celery broker/result backend stay in Redis.
