"""Unit tests for the worker idempotency / progress-heartbeat guard.

Verifies that _run_ingestion:
  - Skips a 'processing' job that still has a live heartbeat (another worker is
    actively progressing it).
  - Reclaims a 'processing' job whose heartbeat has expired (it stopped making
    progress), regardless of how long it had been running.
"""

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.adapters.embeddings.errors import RateLimitError
from api.models.config import AppConfig
from api.tables import documents, job_records, metadata
from tests.unit.fakes import FakeEmbedder, FakeRedis, FakeStore
from worker.tasks import NonRetryableIngestError, _heartbeat_key, _run_ingestion


def _db_factory(tmp_path):
    # NullPool: throwaway test DB — close connections on return, never pool them
    # (avoids an unclosed sqlite3.Connection ResourceWarning at engine GC).
    engine = create_engine(f"sqlite:///{tmp_path / 'guard.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed(factory, job_id: str, status: str, processing_started_at=None) -> None:
    with factory() as s:
        s.execute(insert(documents).values(
            id="doc_1",
            collection_id="col_1",
            filename="test.txt",
            file_type="txt",
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        ))
        s.execute(insert(job_records).values(
            job_id=job_id,
            document_id="doc_1",
            collection_id="col_1",
            filename="test.txt",
            file_type="txt",
            status=status,
            processing_started_at=processing_started_at,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        ))
        s.commit()


def test_processing_job_with_live_heartbeat_is_skipped(tmp_path):
    """A 'processing' job that still has a heartbeat must not be re-ingested —
    even if it has been running a long time (it's progressing, not stuck)."""
    factory = _db_factory(tmp_path)
    _seed(factory, "job_live", "processing")

    redis = FakeRedis()
    redis.set(_heartbeat_key("job_live"), "1")  # another worker is beating

    store = FakeStore()
    result = _run_ingestion(
        "job_live", str(tmp_path / "x.txt"), "col_1", "doc_1", ".txt",
        session_factory=factory,
        embedder=FakeEmbedder(),
        vector_store=store,
        redis_client=redis,
    )
    assert result == 0
    assert store.upserts == []


def test_processing_job_without_heartbeat_is_reclaimed(tmp_path):
    """A 'processing' job whose heartbeat has expired (stopped progressing) is
    reclaimed and re-ingested."""
    factory = _db_factory(tmp_path)
    _seed(factory, "job_dead", "processing")

    txt = tmp_path / "doc.txt"
    txt.write_text("the quick brown fox " * 50)

    store = FakeStore()
    result = _run_ingestion(
        "job_dead", str(txt), "col_1", "doc_1", ".txt",
        session_factory=factory,
        embedder=FakeEmbedder(),
        vector_store=store,
        redis_client=FakeRedis(),  # no heartbeat seeded
    )
    assert result > 0
    assert len(store.upserts) > 0


def test_oversized_stored_object_is_non_retryable(tmp_path):
    """The worker's pre-download size guard rejects an object over the cap with a
    NonRetryableIngestError — before fetching or parsing — so it fails terminally instead of being
    retried (the object won't shrink on a re-run)."""
    factory = _db_factory(tmp_path)
    _seed(factory, "job_big", "processing")  # no heartbeat → reclaimed into the try block

    class _OversizeStorage:
        def object_head(self, key: str) -> int:
            return 2 * 1024 * 1024  # 2 MiB, over the 1 MiB cap injected below

    with pytest.raises(NonRetryableIngestError):
        _run_ingestion(
            "job_big", str(tmp_path / "big.txt"), "col_1", "doc_1", ".txt",
            session_factory=factory,
            embedder=FakeEmbedder(),
            vector_store=FakeStore(),
            redis_client=FakeRedis(),
            storage=_OversizeStorage(),
            config=AppConfig(max_file_size_mb=1),
        )


def test_rate_limit_report_failure_does_not_mask_original(tmp_path):
    """If progress reporting fails while handling a rate limit (e.g. a DB blip in
    document_chunk_ids_at_model), the ORIGINAL RateLimitError must still propagate — otherwise
    ingest_document would misclassify the pause as a hard failure and lose the resume path."""
    factory = _db_factory(tmp_path)
    _seed(factory, "job_rl", "processing")

    txt = tmp_path / "doc.txt"
    txt.write_text("the quick brown fox " * 50)

    class _RateLimitEmbedder(FakeEmbedder):
        def embed_batch(self, texts):
            raise RateLimitError("Gemini 429 RESOURCE_EXHAUSTED")

    class _BlipStore(FakeStore):
        # The resume-set fetch (start of _embed_and_store) succeeds; the SAME call in the
        # rate-limit report handler then blips, simulating a transient DB error at report time.
        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        def document_chunk_ids_at_model(self, collection_id, document_id, model):
            self._calls += 1
            if self._calls == 1:
                return set()  # normal resume-set fetch → embed all chunks
            raise RuntimeError("postgres unavailable")  # the report-time DB blip

    with pytest.raises(RateLimitError):
        _run_ingestion(
            "job_rl", str(txt), "col_1", "doc_1", ".txt",
            session_factory=factory,
            embedder=_RateLimitEmbedder(),
            vector_store=_BlipStore(),
            redis_client=FakeRedis(),
        )
