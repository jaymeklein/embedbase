"""Unit tests for ingestion progress emission (worker/tasks.py).

Verifies _run_ingestion publishes parsing → embedding → storing → done events to
the collection's realtime topic, and that a failing emit never breaks ingestion.
"""

import json

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.services import realtime
from api.tables import documents, job_records, metadata
from worker.tasks import _run_ingestion


class FakeEmbedder:
    @property
    def dimensions(self) -> int:
        return 3

    def embed_batch(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeStore:
    def upsert(self, collection_id, chunks, vectors):
        pass


class RecordingRedis:
    """Records realtime publishes/snapshots; supports the BM25 get/set/incr path."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []
        self.hashes: dict[str, dict[str, str]] = {}
        self._kv: dict[str, str] = {}

    def publish(self, channel, data):
        self.published.append((channel, json.loads(data)))
        return 1

    def hset(self, key, field=None, value=None, mapping=None):
        self.hashes.setdefault(key, {})[str(field)] = str(value)
        return 1

    def expire(self, key, ttl):
        return True

    def get(self, key):
        return self._kv.get(key)

    def set(self, key, value, ex=None):
        self._kv[key] = value

    def incr(self, key):
        self._kv[key] = str(int(self._kv.get(key, 0)) + 1)
        return int(self._kv[key])

    def phases(self):
        return [payload["phase"] for _, payload in self.published]


def _db_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'prog.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed_pending(factory, job_id="job_1", document_id="doc_1", collection_id="col_1"):
    with factory() as s:
        s.execute(insert(documents).values(
            id=document_id, collection_id=collection_id, filename="d.txt", file_type="txt",
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        ))
        s.execute(insert(job_records).values(
            job_id=job_id, document_id=document_id, collection_id=collection_id,
            filename="d.txt", file_type="txt", status="pending",
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        ))
        s.commit()


def test_emits_progress_phases_to_collection_topic(tmp_path):
    factory = _db_factory(tmp_path)
    _seed_pending(factory)
    txt = tmp_path / "d.txt"
    txt.write_text("the quick brown fox jumps over the lazy dog " * 50)

    redis = RecordingRedis()
    result = _run_ingestion(
        "job_1", str(txt), "col_1", "doc_1", ".txt",
        session_factory=factory, embedder=FakeEmbedder(),
        vector_store=FakeStore(), redis_client=redis,
    )

    assert result > 0
    phases = redis.phases()
    # Lifecycle: a parsing start, ≥1 embedding batch, a storing step, then done.
    assert phases[0] == "parsing"
    assert "embedding" in phases
    assert "storing" in phases
    assert phases[-1] == "done"
    # Everything published to the collection's channel, snapshotted by document_id.
    channel = realtime.channel("ingestion:col_1")
    assert all(ch == channel for ch, _ in redis.published)
    assert "doc_1" in redis.hashes.get(channel, {})
    # Terminal event carries done status at 100%.
    done = redis.published[-1][1]
    assert done["status"] == "done"
    assert done["pct"] == 100


def test_failed_emit_never_breaks_ingestion(tmp_path):
    """Every realtime publish raising must not fail the pipeline."""
    factory = _db_factory(tmp_path)
    _seed_pending(factory, job_id="job_2", document_id="doc_2")
    txt = tmp_path / "d2.txt"
    txt.write_text("hello world " * 50)

    class BoomRedis(RecordingRedis):
        def publish(self, channel, data):
            raise RuntimeError("redis down")

    result = _run_ingestion(
        "job_2", str(txt), "col_1", "doc_2", ".txt",
        session_factory=factory, embedder=FakeEmbedder(),
        vector_store=FakeStore(), redis_client=BoomRedis(),
    )
    # Ingestion still completes despite every emit raising.
    assert result > 0
