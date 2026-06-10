"""Integration test for the worker ingestion pipeline.

Runs ``_run_ingestion`` directly with injected fakes (no Redis/Chroma/Celery),
exercising the real parse → chunk → embed → store → BM25 flow against a
temporary SQLite database.
"""

import pytest

pytest.importorskip("tiktoken")
pytest.importorskip("chardet")

from sqlalchemy import create_engine, insert, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from api.models.config import AppConfig  # noqa: E402
from api.tables import documents, job_records, metadata  # noqa: E402
from worker.tasks import _run_ingestion  # noqa: E402


class FakeEmbedder:
    @property
    def dimensions(self) -> int:
        return 3

    def embed_batch(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeStore:
    def __init__(self):
        self.upserts = []

    def upsert(self, collection_id, chunks, vectors):
        self.upserts.append((collection_id, chunks, vectors))


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value

    def incr(self, key):
        self.store[key] = str(int(self.store.get(key, 0)) + 1)
        return int(self.store[key])


def _factory(tmp_path):
    # NullPool: throwaway test DB — close connections on return, never pool them
    # (avoids an unclosed sqlite3.Connection ResourceWarning at engine GC).
    engine = create_engine(f"sqlite:///{tmp_path / 'ingest.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed(factory, *, doc_id, job_id, col_id):
    with factory() as s:
        s.execute(
            insert(documents).values(
                id=doc_id, collection_id=col_id, filename="a.txt",
                file_type=".txt", created_at="t", updated_at="t",
            )
        )
        s.execute(
            insert(job_records).values(
                job_id=job_id, document_id=doc_id, collection_id=col_id,
                filename="a.txt", file_type=".txt", status="pending",
                created_at="t", updated_at="t",
            )
        )
        s.commit()


def test_ingestion_pipeline_end_to_end(tmp_path):
    factory = _factory(tmp_path)
    doc_id, job_id, col_id = "doc_1", "job_1", "col_1"
    _seed(factory, doc_id=doc_id, job_id=job_id, col_id=col_id)

    src = tmp_path / "a.txt"
    src.write_text("Hello world.\n\nA second paragraph.", encoding="utf-8")

    store, rds = FakeStore(), FakeRedis()
    count = _run_ingestion(
        job_id, str(src), col_id, doc_id, ".txt",
        session_factory=factory, embedder=FakeEmbedder(),
        vector_store=store, redis_client=rds, config=AppConfig(),
    )

    assert count >= 1
    assert store.upserts and store.upserts[0][0] == col_id

    with factory() as s:
        row = s.execute(
            select(job_records.c.status, job_records.c.chunk_count).where(
                job_records.c.job_id == job_id
            )
        ).fetchone()
        assert row.status == "done"
        assert row.chunk_count == count

        doc = s.execute(
            select(documents.c.chunk_count).where(documents.c.id == doc_id)
        ).fetchone()
        assert doc.chunk_count == count

    # BM25 corpus + version written.
    assert rds.store[f"bm25:{col_id}:corpus"]
    assert rds.store[f"bm25:{col_id}:version"] == "1"


def test_ingestion_is_idempotent(tmp_path):
    factory = _factory(tmp_path)
    doc_id, job_id, col_id = "doc_2", "job_2", "col_2"
    _seed(factory, doc_id=doc_id, job_id=job_id, col_id=col_id)

    src = tmp_path / "b.txt"
    src.write_text("Only once.", encoding="utf-8")

    store, rds = FakeStore(), FakeRedis()
    kwargs = dict(
        session_factory=factory, embedder=FakeEmbedder(),
        vector_store=store, redis_client=rds, config=AppConfig(),
    )
    _run_ingestion(job_id, str(src), col_id, doc_id, ".txt", **kwargs)
    # Second run sees status == "done" and short-circuits.
    second = _run_ingestion(job_id, str(src), col_id, doc_id, ".txt", **kwargs)
    assert second == 0
    assert len(store.upserts) == 1
