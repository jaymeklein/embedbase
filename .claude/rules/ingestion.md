---
paths:
  - "worker/tasks.py"
  - "worker/celery_app.py"
  - "api/services/ingestion.py"
  - "api/services/indexing.py"
  - "api/services/upload.py"
  - "api/services/storage.py"
  - "api/services/documents.py"
  - "api/services/jobs.py"
  - "api/services/tasks.py"
  - "api/services/realtime.py"
  - "api/adapters/parsers/**"
  - "api/routers/documents.py"
  - "api/routers/indexing.py"
  - "api/routers/jobs.py"
---

# Ingestion pipeline

Flow: **upload → parse → chunk → embed → store**. The DI seam is `worker/tasks.py::_run_ingestion`.

## Sync (API) vs async (worker)
- **API** (`api/routers/documents.py::upload_document`): reject unsupported ext (415), stream to storage with
  a size guard (`api/services/upload.py`, `.tmp` + atomic `os.replace`), insert `documents` + `job_records`,
  enqueue. The **API never imports the worker package** — `api/services/tasks.py::enqueue_ingest` dispatches by
  task-name string over Redis, keeping heavy parser/embedding deps out of the API image.
- **Worker** (`worker/tasks.py::ingest_document → _run_ingestion`): **size-guard** the stored object
  (`storage.object_head` vs `resolve_max_bytes`, before download — a presigned upload's PUT URL is reusable, so
  the object is mutable until ingest reads it), fetch bytes to a local temp (`storage.fetch_to_temp`), **parse**
  (`get_parser(...)`), **chunk** (`api/services/ingestion.py::sliding_window`, tiktoken `cl100k_base`, called
  inside the txt/markdown parsers), **embed + store** in resumable batches (`_embed_and_store` →
  `embedder.embed_batch` → `vector_store.upsert`), mark done.
- **BM25 is not a stage** — it's the stored `chunks.text_tsv` maintained by the upsert ([`vector-db.md`](vector-db.md)).
- **Upload entry points + retention**: REST multipart (`documents.ingest`, streamed + size-guarded), the
  presigned two-step for MCP (`documents.create_upload` → `awaiting_upload` row + presigned PUT →
  `documents.confirm_upload`, [`mcp.md`](mcp.md)), and the master-only container path (`ingest_local_path`). All
  take a per-file **`retention_days` (1-30; omit = permanent)** → `_expiry` stamps `documents.expires_at` (422
  outside the band), which the purge sweep reaps. This **supersedes** the old global `storage.temp_retention_hours`
  + boolean `temporary` (the config field is kept for back-compat but no longer read by uploads).

## Jobs, status, progress
- **`job_records` table.** Status: `pending → processing → done | failed | rate_limited`. `_claim_job` atomically
  flips pending→processing; a **Redis heartbeat** `ingest:hb:{job_id}` (TTL 600s) is refreshed on each progress event.
- **Progress**: the `emit()` closure publishes via `api/services/realtime.py` to two pub/sub topics
  (`ingestion:{col}` for the Documents view, `ingestion-queue` for the Queue tab) with `snapshot_key=document_id`
  so late joiners get replayed the latest per-document state. Phases: `parsing/embedding/storing/done/failed/rate_limited`.
  PDF page callbacks are coalesced to ~1 frame/percent.
- **Queue read side** (grant-scoped): `api/routers/jobs.py` — `GET /ingestion/jobs` (paginated/filtered),
  `GET /ingestion/jobs/stats`. Both `require_auth` and narrow to the caller's readable collections via
  `permissions.readable_collection_scope` (master/admin see all); the `ingestion-queue` WS is filtered the
  same way per event. Bulk `retry-failed` stays master/admin-only ([`permissions.md`](permissions.md)).

## DI shape (why it's testable)
`_run_ingestion(job_id, file_path, collection_id, document_id, file_type, *, session_factory=None,
embedder=None, vector_store=None, redis_client=None, config=None, storage=None)`. Each `None` resolves to a
lazy prod singleton; unit tests pass fakes ([`testing.md`](testing.md)). Keep new stages inject-everything.

## Retries / failure / resume
- `ingest_document` is `bind=True, max_retries=3, retry_backoff=True`. Except ladder: `SoftTimeLimitExceeded`
  first (fail, no retry) → **rate limit** (`_is_rate_limit`: typed `RateLimitError` / httpx 429 / narrow phrase —
  deliberately not bare "429"/"quota") → other (fail + `self.retry`).
- **Rate limit is not a failure**: `_pause_embedding(delay)` sets a **global** TTL key `EMBEDDING_PAUSE_KEY`,
  job → `rate_limited`, returns None (acks). One 429 pauses **all** ingestion so N queued docs don't each
  re-hit the exhausted quota (circuit breaker); `/ingestion/jobs/stats` surfaces remaining backoff.
- **Resume-aware, never restart from chunk 0**: `_embed_and_store` skips chunks already in
  `vector_store.document_chunk_ids_at_model(col, doc, model)`; per-batch upsert persists immediately.
  Deterministic `make_chunk_id` makes re-parse yield the same ids; a model change re-embeds only stale chunks.
- **Beat sweeps** (`worker/celery_app.py`, embedded `-B`, every 300s): `retry_rate_limited_ingests` requeues
  `rate_limited` + stale-heartbeat `processing` + orphaned `pending` via `_requeue_by_status` — re-enqueued as
  **fresh** Celery tasks (uncapped lineage), **same `job_id`** (so `_claim_job` dedups), bounded batch. Gated
  off while paused. `purge_expired_documents` reaps due-`expires_at` docs **and** abandoned presigned-upload
  reservations (`status="awaiting_upload"` older than `_AWAITING_UPLOAD_TTL_HOURS`).
- **Retry all failed**: `POST /ingestion/jobs/retry-failed` → `api/services/jobs.py::reprocess_failed_documents`
  re-enqueues every **currently**-failed doc (latest attempt failed) matching the queue's active filters, each
  via `documents.py::reprocess_document` (idempotent). **Single retry**: `POST /documents/{id}/reprocess`.
- The ingestion-queue **refetch-storm fix is frontend-only** (`ui/src/realtime/useIngestionQueue.ts` debounces
  invalidation) — no backend change; don't look for it in `api/`/`worker/`.

## Changing a stage
Stage → file: parse = `api/adapters/parsers/<ext>.py` (+ registry in `parsers/__init__.py`); chunk =
`api/services/ingestion.py::sliding_window`; embed = `api/adapters/embeddings/<provider>.py`; store + BM25 =
`api/adapters/vector_store/pgvector.py`; storage backend = `api/services/storage.py`. All resolve through
`get_*()` factories — add a backend = new file + one factory branch ([`architecture.md`](architecture.md)).
Knobs live in `api/models/config.py` ([`configuration.md`](configuration.md)).

## Hard rules / gotchas
- **Graceful degradation**: progress/status reporting is best-effort and wrapped — it must never mask the
  original exception or 500 an ingest. Heartbeat/Redis reads **fail open** (`_job_alive` assumes alive on error,
  so a blip can't cause double-processing). Misclassifying a 429 as a hard failure loses the infinite-resume path.
- **Idempotency**: `_claim_job` skips `done` and live `processing`; requeue/reprocess keep the same `job_id`;
  delete is idempotent. **Storage key is single-source-of-truth** — import `document_key`, never re-encode it.
- RPM throttle: module-level `_RpmLimiter` (single-process — valid because worker `--concurrency=1`).
- Effective tags (workspace→collection→document union) are folded into each chunk's metadata at embed time.
