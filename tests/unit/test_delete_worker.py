"""Unit tests for the worker delete task: vector cleanup + hard-delete.

Lexical/BM25 is the STORED ``chunks.text_tsv`` column (Phase 3): deleting the
chunks drops FTS with them, so there is no separate corpus to prune.
"""

from worker.tasks import delete_document


class FakeRedis:
    """In-memory Redis stub (delete no longer touches BM25, but the task still
    resolves the redis singleton for realtime — kept as a harmless double)."""

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

    assert fake_session.execute.called  # row delete + remaining-docs check
    fake_session.commit.assert_called_once()


def test_delete_task_drops_collection_when_emptied(monkeypatch) -> None:
    from unittest.mock import MagicMock

    fake_vs = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_session.execute.return_value.first.return_value = None  # no documents remain

    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", FakeRedis())
    monkeypatch.setattr("worker.tasks.SessionLocal", MagicMock(return_value=fake_session))

    delete_document.apply(args=["doc1", "col1"])

    fake_vs.delete_collection.assert_called_once_with("col1")


def test_delete_task_keeps_collection_when_docs_remain(monkeypatch) -> None:
    from unittest.mock import MagicMock

    fake_vs = MagicMock()
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_session.execute.return_value.first.return_value = ("doc2",)  # a document remains

    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", FakeRedis())
    monkeypatch.setattr("worker.tasks.SessionLocal", MagicMock(return_value=fake_session))

    delete_document.apply(args=["doc1", "col1"])

    fake_vs.delete_collection.assert_not_called()


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
