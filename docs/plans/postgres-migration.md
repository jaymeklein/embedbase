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
| 0 | Land pending work on `main` | clean base to branch from | `[x]` |
| 1 | Relational store → Postgres | "storage": metadata off SQLite | `[x]` |
| 2 | Vectors → pgvector (already coded) | "storage": vectors off Chroma | `[x]` |
| 3 | Lexical/BM25 → Postgres FTS | "indexing": kill the Redis corpus blob | `[x]` |
| 4 | Pre-filter pushdown + single round-trip hybrid | "query times" | `[~]` |

Went further than planned on Phase 2: rather than merely *supporting* pgvector
as one of several backends, Chroma and Qdrant were deleted outright (adapters,
config, docker-compose services) — Postgres is now the only vector store, not
a config choice. `docker-compose.yml` bundles one `postgres` (`paradedb/paradedb`
= pgvector + pg_search/BM25) service that the relational engine (`DATABASE_URL`),
the vector store, and lexical BM25 (`vector_store.host`) all point at by default.

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
- **`docker-compose.yml`** — bundles `postgres` (`paradedb/paradedb`, which ships
  pgvector **and** pg_search/BM25) as a first-class service; api+worker point both
  `DATABASE_URL` and the vector store at it by default. Chroma/Qdrant compose
  files and adapters are gone.

## What is NOT on Postgres (the remaining gap)

Nothing on the storage/indexing side — metadata, vectors, and lexical/BM25 all
live in Postgres. The only remaining work is **query-time** (Phase 4): filtering
is still applied *after* the vector search, and hybrid is still two calls fused
in Python rather than one SQL statement.

---

## Phase 0 — Land the pending work first `[x]`

Merged via PR #50.

---

## Phase 1 — Relational store on Postgres `[x]`

- `api/db.py` / `worker/db.py` read `DATABASE_URL` (default
  `sqlite+aiosqlite:///…` / `sqlite:///…` for dev back-compat). `psycopg` (v3)
  drives Postgres in both async (api) and sync (worker) mode from one package.
- `_set_sqlite_pragmas` is only registered when the engine's dialect is `sqlite`
  — no-op on PG.
- **Dialect-aware upserts**: `api/sql_compat.dialect_insert(bind, table)` picks
  `postgresql.insert` vs `sqlite.insert` by the bound dialect. Used by
  `api/services/tags.py` and `worker/tasks.py` (both previously SQLite-only).
- **Alembic audit**: all 5 migrations use portable SQLAlchemy core types/ops
  (no SQLite-only constructs) — ported without touching the migration bodies.
  `render_as_batch` (SQLite-only rebuild-the-table emulation) is now gated to
  the `sqlite` dialect in `alembic/env.py`; Postgres uses direct `ALTER TABLE`.
- `docker-compose.yml`: `DATABASE_URL` is derived from the same `POSTGRES_*`
  vars the vector store uses, pointing both at the one bundled `postgres`
  service (Phase 2's consolidation, done here since the container already
  exists for vectors).

---

## Phase 2 — Vectors on pgvector `[x]`

Went beyond "point at the same instance": Chroma and Qdrant were removed
entirely (`ChromaAdapter`, `QdrantAdapter`, the backend registry, their
config fields and docker-compose services) — `PgvectorAdapter` is now the only
vector store, constructed directly (no registry indirection), and it shares
the Postgres instance Phase 1's relational engine uses.

- `embeddinggemma` = 768-dim → `chunks.embedding vector(768)`. Dimensions flow
  from `embedder.dimensions` into `get_vector_store(config, dims)`.
- Re-ingest is still the migration path for existing Chroma data (deterministic
  `make_chunk_id` makes it idempotent/resumable) — no data-copy tool was written.

---

## Phase 3 — Lexical/BM25 in Postgres FTS `[x]` (the "indexing" win)

Retired the Redis corpus blob; keyword search now runs **in the DB, incrementally**.

- `chunks` gained a **pg_search BM25 index** (`chunks_bm25_idx` over
  `(id, text, collection_id)`, `key_field='id'`, built in `_bootstrap_schema`) —
  maintained incrementally on every upsert, no separate write path, no
  duplication. This is **real Okapi BM25** (IDF saturation + avgdl), not native
  `ts_rank`.
- `api/services/search.py` scores via `PgvectorAdapter.bm25_scores`
  (`text ||| $query` match + `pdb.score(id)`), scoped to the vector candidate set
  (`id = ANY($3)`) and keyed by `chunk_id`. RRF fusion is rank-based, so it was
  unaffected by the score-source swap. `redis_client` dropped from the whole
  search call chain.
- **Deleted**: `_update_bm25_index` / `_delete_from_bm25_index` /
  `_reindex_document_bm25` / `_reindex_collection_bm25` (corpus writes), the
  in-process `_bm25_cache` + `_get_bm25_scores`, `search.bm25_cache_ttl` config,
  `api/models/redis.py` (`Corpus`/`CorpusConfig`) and `api/services/redis/redis.py`.
  The `index_document` / `index_collection` tasks are now no-ops (FTS is
  auto-maintained) kept only so the manual /index endpoints stay valid.
- **"indexed" redefined**: `api/services/indexing.py` and the documents listing
  now derive it from `documents.chunk_count > 0` (has stored/FTS-searchable
  chunks) instead of corpus membership — no Redis dependency.
- Tag suggestion (`api/services/tag_suggest.py`) pulls entity text from the
  vector store (`collection_texts` / `iter_document_chunks`) instead of the corpus.

This killed, in one move: O(collection) rewrite per ingest, per-process rebuild,
the cold-start blob fetch, the per-process cache (so it scales to N API replicas),
and the text duplication.

> **Ranking model:** we use **ParadeDB `pg_search`** for true Okapi BM25 (IDF
> saturation + avgdl) rather than native `ts_rank`, so keyword matching keeps the
> same relevance model as the old in-process `BM25Okapi` — now maintained
> incrementally in the DB. The BM25 SQL (`|||` / `pdb.score`) runs only
> against real Postgres, so it is not exercised by the SQLite-based test suite;
> verify against a live ParadeDB instance.
>
> **Candidate scope:** BM25 re-ranks the vector candidate set (same recall
> ceiling as the pre-migration code, which also only re-ranked candidates). True
> full-collection lexical retrieval fused with vectors is Phase 4's single
> round-trip hybrid.

---

## Phase 4 — Query-time wins `[~]`

**Delivered: pre-filter pushdown (item 1).** `SearchFilters` (language/filename/tags)
now folds into the vector search's SQL `WHERE` via `_metadata_filter_sql`
(`metadata->>'language'`/`filename` exact-match, `metadata->'tags' @> …::jsonb`
containment = "all requested tags present"), so the DB returns only matching rows —
correct top-k under restrictive filters, fewer rows scanned, no over-fetch. BM25
inherits it for free (it only re-ranks the already-filtered candidate set).
`apply_filters` stays in `api/services/search.py` as a backend-agnostic guard (a
no-op once the WHERE has run; the only filter on the in-memory test fakes). Verified
against a live ParadeDB instance (tags OR-subset, tags-AND, language, filename, and
no-match cases). Item 3 (no BM25 cold-start cliff) was already realized in Phase 3.

**Deferred: single round-trip hybrid (item 2).** Fusing the vector + BM25 calls into
one SQL statement (ParadeDB's CTE + `RANK()` + RRF-in-SQL pattern) is a real rewrite:
it would replace the tested, alpha-weighted Python RRF (`_reciprocal_rank_fusion` +
`api/services/bm25.py`) and the SEMANTIC_ONLY/BM25-only fallback logic with SQL the
SQLite test suite can't exercise — for a latency gain this plan itself calls marginal
(warm single-node fusion ≈ one round-trip). Left as an explicit call rather than
torn out speculatively.

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
2. **Single round-trip hybrid.** Today hybrid is still two calls fused in Python
   (vector `search` + BM25 `bm25_scores`, both on the adapter). In Postgres it can
   be **one** SQL statement: vector `ORDER BY embedding <=> $1`, `pdb.score`,
   and the filter `WHERE`, combined. One round-trip, less app-side glue, better
   tail latency.
3. **No BM25 cold-start cliff** — *already realized in Phase 3.* The old
   in-process cache paid a full blob fetch + tokenize + `BM25Okapi` rebuild on
   every restart/reindex (linear in corpus). The pg_search BM25 index is
   incremental — no rebuild, no cold p99 spike, and replica-safe (no per-process
   cache to warm N times).

**Honest trade-off:** steady-state warm single-node `BM25Okapi` may be marginally
faster per-query than a pg_search round-trip; Phase 3 traded that small warm-path
cost for no cold-start cliff and horizontal scalability. Phase 4's remaining win
is pre-filtering + one round-trip.

- Depends on Phase 1–3 (needs metadata + vectors + BM25 index co-located in PG).
- Overlaps `retrieval-upgrade-plan.md`'s "filtering is post-ranking" note — this
  is where that gets fixed.

---

## Data migration

- **Dev (now):** one test collection → **re-ingest**, don't build a migration tool.
  Idempotent via deterministic chunk ids.
- **Prod (if/when):** `pgloader` for the relational tables (SQLite→PG in one pass);
  vectors either copied or re-embedded (re-embed is simplest and idempotent).
  Lexical needs nothing — the pg_search BM25 index builds from `chunks.text` on load.

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
