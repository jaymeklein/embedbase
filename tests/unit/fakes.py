"""Shared in-memory test doubles for the worker ingestion pipeline and adjacent tests.

``_run_ingestion`` depends on an embedder and a vector store; these fakes stand in for
both so the pipeline can be exercised without a real model or pgvector. Kept in one
module (alongside ``fake_redis``) so a contract — the resume/idempotency behaviour, the
Redis surface, the upload stub — lives in a single place: every test sees the same
double, and a change to that contract is made once here rather than in each test file.
"""

from __future__ import annotations

import io


class FakeEmbedder:
    """Deterministic 3-dimensional embedder — every text maps to the same vector."""

    @property
    def dimensions(self) -> int:
        return 3

    def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class FakeStore:
    """In-memory vector-store double for the ingestion pipeline.

    Records every ``upsert`` as a ``(collection_id, chunks, vectors)`` tuple (read by
    tests via ``.upserts``) and remembers the ``embedding_model`` each chunk was stored
    at. That lets ``document_chunk_ids_at_model`` report which chunks already exist for a
    given model — the resume set ``_run_ingestion`` uses to skip re-embedding work that a
    previous run already completed.
    """

    def __init__(self) -> None:
        self.upserts: list = []
        self._models: dict = {}  # chunk_id -> embedding_model it was stored at

    def upsert(
        self, collection_id: str, chunks: list, vectors: list, model: str | None = None
    ) -> None:
        self.upserts.append((collection_id, chunks, vectors))
        for c in chunks:
            self._models[c.id] = model

    def document_chunk_ids_at_model(
        self, collection_id: str, document_id: str, model: str | None
    ) -> set:
        # chunk ids already stored for this document at the given model (resume set).
        return {
            c.id
            for _cid, chunks, _vec in self.upserts
            for c in chunks
            if c.metadata.document_id == document_id and self._models.get(c.id) == model
        }

    def iter_document_chunks(self, collection_id: str, document_id: str) -> list:
        # (chunk_id, document_id, text) triples for the resume/skip check.
        return [
            (c.id, c.metadata.document_id, c.text)
            for _cid, chunks, _vec in self.upserts
            for c in chunks
            if c.metadata.document_id == document_id
        ]


class FakeRedis:
    """In-memory Redis double for the worker paths — heartbeat/claim, BM25 counters, the
    retry-pending marker, and the realtime singleton.

    Implements just the surface those touch (get/set/incr/exists/delete) and records
    per-key TTLs (unused by most tests, asserted by a few). Distinct from
    ``tests.unit.fake_redis.FakeRedis``, which models the config hot-reload pub/sub
    surface (publish/hset/pubsub) instead — different subsystem, different methods.
    """

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})
        self.ttls: dict[str, int | None] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttls[key] = ex

    def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    def delete(self, key: str) -> None:
        self.store.pop(key, None)
        self.ttls.pop(key, None)


class FakeUpload:
    """Minimal stand-in for starlette's UploadFile (``.size`` + async ``.read``)."""

    def __init__(self, data: bytes, size: int | None = None) -> None:
        self._buf = io.BytesIO(data)
        self.size = size

    async def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)
