"""Unit tests for the worker idempotency / progress-heartbeat guard.

Verifies that _run_ingestion:
  - Skips a 'processing' job that still has a live heartbeat (another worker is
    actively progressing it).
  - Reclaims a 'processing' job whose heartbeat has expired (it stopped making
    progress), regardless of how long it had been running.
"""

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.tables import documents, job_records, metadata
from worker.tasks import _heartbeat_key, _run_ingestion


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

    def iter_document_chunks(self, collection_id: str, document_id: str) -> list:
        out: list = []
        for _cid, chunks, _vec in self.upserts:
            out.extend(
                (c.id, c.metadata.document_id, c.text)
                for c in chunks
                if c.metadata.document_id == document_id
            )
        return out


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = value

    def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

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
