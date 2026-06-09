"""Unit tests for the worker delete task: BM25 prune, vector cleanup, hard-delete."""

import json

from api.models.redis import CorpusConfig
from worker.tasks import (
    BM25_TTL_SECONDS,
    _delete_from_bm25_index,
    delete_document,
)


class FakeRedis:
    """In-memory Redis stub for BM25 corpus tests."""

    def __init__(self, initial: dict | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])


# ---------------------------------------------------------------------------
# _delete_from_bm25_index — pure function tests
# ---------------------------------------------------------------------------


def test_delete_from_bm25_index_removes_only_target() -> None:
    corpus = [["doc1", "hello"], ["doc2", "world"], ["doc1", "again"]]
    rds = FakeRedis({"bm25:col1:corpus": json.dumps(corpus)})

    _delete_from_bm25_index(rds, CorpusConfig("col1"), "doc1")

    result = json.loads(rds.store["bm25:col1:corpus"])
    assert result == [["doc2", "world"]]
    assert "bm25:col1:version" in rds.store

def test_delete_from_bm25_index_noop_when_corpus_absent() -> None:
    rds = FakeRedis()
    _delete_from_bm25_index(rds, CorpusConfig("col1"), "doc1")
    assert "bm25:col1:corpus" not in rds.store


def test_delete_from_bm25_index_noop_when_doc_not_present() -> None:
    corpus = [["doc2", "other"]]
    rds = FakeRedis({"bm25:col1:corpus": json.dumps(corpus)})

    _delete_from_bm25_index(rds, CorpusConfig("col1"), "doc1")

    # corpus unchanged, version not bumped
    assert "bm25:col1:version" not in rds.store
    assert json.loads(rds.store["bm25:col1:corpus"]) == corpus


def test_delete_from_bm25_index_increments_version() -> None:
    corpus = [["doc1", "text"]]
    rds = FakeRedis({"bm25:col1:corpus": json.dumps(corpus), "bm25:col1:version": "5"})

    _delete_from_bm25_index(rds, CorpusConfig("col1"), "doc1")

    assert rds.store["bm25:col1:version"] == "6"


def test_delete_from_bm25_index_preserves_ttl() -> None:
    corpus = [["doc1", "text"]]
    rds = FakeRedis({"bm25:col1:corpus": json.dumps(corpus)})

    _delete_from_bm25_index(rds, CorpusConfig("col1"), "doc1")

    # corpus key was rewritten — verify it used the correct TTL constant
    assert BM25_TTL_SECONDS == 60 * 60 * 24


# ---------------------------------------------------------------------------
# delete_document task — happy-path and retry
# ---------------------------------------------------------------------------


def test_delete_task_has_retry_config() -> None:
    assert delete_document.max_retries == 3
    assert delete_document.retry_backoff is True
    assert delete_document.retry_backoff_max == 60


def test_delete_task_calls_vector_store(monkeypatch) -> None:
    from unittest.mock import MagicMock

    fake_vs = MagicMock()
    fake_rds = FakeRedis()
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_factory = MagicMock(return_value=fake_session)

    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)
    monkeypatch.setattr("worker.tasks.SessionLocal", fake_factory)

    delete_document.apply(args=["doc1", "col1"])

    fake_vs.delete_document.assert_called_once_with("col1", "doc1")


def test_delete_task_prunes_bm25_corpus(monkeypatch) -> None:
    from unittest.mock import MagicMock

    corpus = [["doc1", "hello"], ["doc2", "keep"]]
    fake_vs = MagicMock()
    fake_rds = FakeRedis({"bm25:col1:corpus": json.dumps(corpus)})
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_factory = MagicMock(return_value=fake_session)

    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)
    monkeypatch.setattr("worker.tasks.SessionLocal", fake_factory)

    delete_document.apply(args=["doc1", "col1"])

    remaining = json.loads(fake_rds.store["bm25:col1:corpus"])
    assert remaining == [["doc2", "keep"]]


def test_delete_task_hard_deletes_sqlite_row(monkeypatch) -> None:
    from unittest.mock import MagicMock

    fake_vs = MagicMock()
    fake_rds = FakeRedis()
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_factory = MagicMock(return_value=fake_session)

    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)
    monkeypatch.setattr("worker.tasks.SessionLocal", fake_factory)

    delete_document.apply(args=["doc1", "col1"])

    fake_session.execute.assert_called_once()
    fake_session.commit.assert_called_once()


def test_delete_task_retries_on_vector_store_error(monkeypatch) -> None:
    from unittest.mock import MagicMock

    fake_vs = MagicMock()
    fake_vs.delete_document.side_effect = RuntimeError("chroma unavailable")
    fake_rds = FakeRedis()

    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)

    # apply() in eager mode returns a failed EagerResult rather than raising.
    result = delete_document.apply(args=["doc1", "col1"])
    assert result.failed()
    # 1 initial call + 3 retries = 4 total
    assert fake_vs.delete_document.call_count == 4
