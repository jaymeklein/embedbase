"""Unit tests for the worker delete task: vector cleanup + hard-delete.

Lexical/BM25 is the STORED ``chunks.text_tsv`` column (Phase 3): deleting the
chunks drops FTS with them, so there is no separate corpus to prune.
"""

from tests.unit.fakes import FakeRedis
from worker.tasks import delete_document

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


def test_delete_task_removes_orphan_job_record(tmp_path, monkeypatch) -> None:
    """delete_document hard-deletes the job_records row too, so a purged/deleted document
    can't leave an orphan that the rate-limit sweep keeps re-enqueuing."""
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine, insert, select
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import NullPool

    from api.tables import documents, job_records, metadata

    engine = create_engine(f"sqlite:///{tmp_path / 'del.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with factory() as s:
        s.execute(insert(documents).values(
            id="doc1", collection_id="col1", filename="a.pdf", file_type=".pdf",
            created_at="t", updated_at="t",
        ))
        s.execute(insert(job_records).values(
            job_id="job1", document_id="doc1", collection_id="col1", filename="a.pdf",
            file_type=".pdf", status="rate_limited", created_at="t", updated_at="t",
        ))
        s.commit()

    monkeypatch.setattr("worker.tasks._vector_store_singleton", MagicMock())
    monkeypatch.setattr("worker.tasks._redis_singleton", FakeRedis())
    monkeypatch.setattr("worker.tasks.SessionLocal", factory)
    monkeypatch.setattr("worker.tasks._delete_stored_object", lambda *a: None)

    delete_document.apply(args=["doc1", "col1"])

    with factory() as s:
        assert (
            s.execute(select(job_records.c.job_id).where(job_records.c.document_id == "doc1"))
            .first()
            is None  # the orphan job row is gone
        )
        assert (
            s.execute(select(documents.c.id).where(documents.c.id == "doc1")).first() is None
        )
