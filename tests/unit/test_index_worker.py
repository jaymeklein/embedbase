"""Unit tests for the BM25 (re)index worker helpers."""

import json
from unittest.mock import MagicMock

from worker.tasks import (
    _reindex_collection_bm25,
    _reindex_document_bm25,
    index_collection,
    index_document,
)


class FakeRedis:
    """In-memory Redis stub tracking values and TTLs."""

    def __init__(self, initial: dict | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})
        self.ttls: dict[str, int | None] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value
        self.ttls[key] = ex

    def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])


class FakeVectorStore:
    """Returns canned (chunk_id, document_id, text) triples per document."""

    def __init__(self, chunks: dict[str, list[tuple[str, str, str]]]) -> None:
        self._chunks = chunks

    def iter_document_chunks(
        self, collection_id: str, document_id: str
    ) -> list[tuple[str, str, str]]:
        return list(self._chunks.get(document_id, []))


class _Result:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[str]]:
        return self._rows


class FakeSession:
    """Minimal session_factory context manager returning fixed doc ids."""

    def __init__(self, doc_ids: list[str]) -> None:
        self._doc_ids = doc_ids

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def execute(self, *_: object, **__: object) -> _Result:
        return _Result([(d,) for d in self._doc_ids])


def test_reindex_document_writes_corpus_without_ttl():
    rds = FakeRedis()
    vs = FakeVectorStore({"doc1": [("c1", "doc1", "hello world"), ("c2", "doc1", "more")]})
    n = _reindex_document_bm25(rds, vs, "col1", "doc1")
    assert n == 2
    assert json.loads(rds.store["bm25:col1:corpus"]) == [
        ["c1", "doc1", "hello world"],
        ["c2", "doc1", "more"],
    ]
    assert rds.store["bm25:col1:version"] == "1"
    assert rds.ttls["bm25:col1:corpus"] is None


def test_reindex_document_replaces_only_its_own_entries():
    existing = [["old", "doc1", "stale"], ["k", "doc2", "keep"]]
    rds = FakeRedis({"bm25:col1:corpus": json.dumps(existing)})
    vs = FakeVectorStore({"doc1": [("c1", "doc1", "fresh")]})
    _reindex_document_bm25(rds, vs, "col1", "doc1")
    corpus = json.loads(rds.store["bm25:col1:corpus"])
    assert ["k", "doc2", "keep"] in corpus
    assert ["c1", "doc1", "fresh"] in corpus
    assert ["old", "doc1", "stale"] not in corpus


def test_reindex_document_with_no_chunks_clears_the_document():
    rds = FakeRedis({"bm25:col1:corpus": json.dumps([["old", "doc1", "stale"]])})
    vs = FakeVectorStore({})  # document absent from the vector store
    n = _reindex_document_bm25(rds, vs, "col1", "doc1")
    assert n == 0
    assert json.loads(rds.store["bm25:col1:corpus"]) == []


def test_reindex_collection_rebuilds_corpus_wholesale():
    rds = FakeRedis({"bm25:col1:corpus": json.dumps([["stale", "x", "gone"]])})
    vs = FakeVectorStore({
        "doc1": [("c1", "doc1", "alpha")],
        "doc2": [("c2", "doc2", "beta"), ("c3", "doc2", "gamma")],
    })
    n = _reindex_collection_bm25(rds, vs, lambda: FakeSession(["doc1", "doc2"]), "col1")
    assert n == 3
    corpus = json.loads(rds.store["bm25:col1:corpus"])
    assert ["stale", "x", "gone"] not in corpus  # wholesale replace, not append
    assert len(corpus) == 3
    assert rds.store["bm25:col1:version"] == "1"
    assert rds.ttls["bm25:col1:corpus"] is None


# --- celery task wrappers ---------------------------------------------------


def test_index_document_task_writes_corpus(monkeypatch):
    fake_vs = MagicMock()
    fake_vs.iter_document_chunks.return_value = [("c1", "doc1", "hello")]
    fake_rds = FakeRedis()
    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)

    index_document.apply(args=["doc1", "col1"])

    assert json.loads(fake_rds.store["bm25:col1:corpus"]) == [["c1", "doc1", "hello"]]


def test_index_collection_task_rebuilds_corpus(monkeypatch):
    fake_vs = MagicMock()
    fake_vs.iter_document_chunks.return_value = [("c1", "doc1", "hello")]
    fake_rds = FakeRedis()
    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)
    monkeypatch.setattr("worker.tasks.SessionLocal", lambda: FakeSession(["doc1"]))

    index_collection.apply(args=["col1"])

    assert json.loads(fake_rds.store["bm25:col1:corpus"]) == [["c1", "doc1", "hello"]]
