"""Unit tests for the worker idempotency / stale-processing guard.

Verifies that _run_ingestion:
  - Skips a job whose status is 'processing' with a fresh timestamp.
  - Re-processes (reclaims) a job whose status is 'processing' but whose
    processing_started_at is older than STALE_PROCESSING_SECONDS.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.tables import documents, job_records, metadata
from worker.tasks import STALE_PROCESSING_SECONDS, _run_ingestion


class FakeEmbedder:
    @property
    def dimensions(self) -> int:
        return 3

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeStore:
    def __init__(self) -> None:
        self.upserts: list = []

    def upsert(self, collection_id: str, chunks: list, vectors: list) -> None:
        self.upserts.append((collection_id, chunks, vectors))


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    def incr(self, key: str) -> int:
        self._store[key] = str(int(self._store.get(key, 0)) + 1)
        return int(self._store[key])


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


def test_fresh_processing_job_is_skipped(tmp_path):
    """A job in 'processing' with a fresh timestamp must not be re-ingested."""
    factory = _db_factory(tmp_path)
    now = datetime.now(UTC).replace(tzinfo=None)
    _seed(factory, "job_fresh", "processing", processing_started_at=now)

    store = FakeStore()
    result = _run_ingestion(
        "job_fresh", str(tmp_path / "x.txt"), "col_1", "doc_1", ".txt",
        session_factory=factory,
        embedder=FakeEmbedder(),
        vector_store=store,
        redis_client=FakeRedis(),
    )
    assert result == 0
    assert store.upserts == []


def test_stale_processing_job_is_reclaimed(tmp_path):
    """A job in 'processing' older than STALE_PROCESSING_SECONDS must be re-ingested."""
    factory = _db_factory(tmp_path)
    stale_ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=STALE_PROCESSING_SECONDS + 60)
    _seed(factory, "job_stale", "processing", processing_started_at=stale_ts)

    txt = tmp_path / "doc.txt"
    txt.write_text("the quick brown fox " * 50)

    store = FakeStore()
    result = _run_ingestion(
        "job_stale", str(txt), "col_1", "doc_1", ".txt",
        session_factory=factory,
        embedder=FakeEmbedder(),
        vector_store=store,
        redis_client=FakeRedis(),
    )
    assert result > 0
    assert len(store.upserts) > 0
