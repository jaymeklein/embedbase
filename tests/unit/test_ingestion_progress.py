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
    def __init__(self):
        self.chunks = []
        self._models = {}  # chunk_id -> embedding_model it was stored at

    def upsert(self, collection_id, chunks, vectors, model=None):
        self.chunks.extend(chunks)
        for c in chunks:
            self._models[c.id] = model

    def document_chunk_ids_at_model(self, collection_id, document_id, model):
        return {
            c.id
            for c in self.chunks
            if c.metadata.document_id == document_id and self._models.get(c.id) == model
        }

    def iter_document_chunks(self, collection_id, document_id):
        return [
            (c.id, c.metadata.document_id, c.text)
            for c in self.chunks
            if c.metadata.document_id == document_id
        ]


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
    assert "embedding" in phases and "storing" in phases

    # Fanned out to both the collection topic (Documents view) and the global
    # ingestion-queue topic (Ingestion Queue tab), each snapshotted by document_id.
    col_channel = realtime.channel("ingestion:col_1")
    queue_channel = realtime.channel("ingestion-queue")
    assert {ch for ch, _ in redis.published} == {col_channel, queue_channel}
    col_phases = [p["phase"] for ch, p in redis.published if ch == col_channel]
    assert col_phases[0] == "parsing" and col_phases[-1] == "done"
    assert "doc_1" in redis.hashes.get(col_channel, {})
    assert "doc_1" in redis.hashes.get(queue_channel, {})

    # Queue events carry file identity and the per-chunk labels (live list).
    queue_events = [p for ch, p in redis.published if ch == queue_channel]
    assert all(p["filename"] == "d.txt" and p["collection_name"] for p in queue_events)
    embed_chunks = [p for p in queue_events if p["phase"] == "embedding" and p.get("chunks")]
    assert embed_chunks and "index" in embed_chunks[0]["chunks"][0]

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


class _CountingEmbedder(FakeEmbedder):
    """FakeEmbedder that records how many chunk texts it embedded."""

    def __init__(self):
        self.embedded = 0

    def embed_batch(self, texts):
        self.embedded += len(texts)
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_reingest_resumes_and_skips_stored_chunks(tmp_path):
    """A re-run over a document already in the store embeds nothing new (resume) but
    still reports the full chunk count."""
    from sqlalchemy import insert

    factory = _db_factory(tmp_path)
    _seed_pending(factory, job_id="job_r1", document_id="doc_r")
    txt = tmp_path / "r.txt"
    txt.write_text("the quick brown fox jumps over the lazy dog " * 80)

    store = FakeStore()
    emb1 = _CountingEmbedder()
    n1 = _run_ingestion(
        "job_r1", str(txt), "col_1", "doc_r", ".txt",
        session_factory=factory, embedder=emb1, vector_store=store, redis_client=RecordingRedis(),
    )
    assert n1 > 0
    assert emb1.embedded == n1  # fresh run embedded every chunk

    # Second job for the SAME document, store already populated → resume.
    with factory() as s:
        s.execute(insert(job_records).values(
            job_id="job_r2", document_id="doc_r", collection_id="col_1",
            filename="r.txt", file_type="txt", status="pending",
            created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        ))
        s.commit()

    emb2 = _CountingEmbedder()
    n2 = _run_ingestion(
        "job_r2", str(txt), "col_1", "doc_r", ".txt",
        session_factory=factory, embedder=emb2, vector_store=store, redis_client=RecordingRedis(),
    )
    assert n2 == n1  # same total
    assert emb2.embedded == 0  # resumed: nothing re-embedded
