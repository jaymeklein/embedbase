"""Unit tests for the BM25 (re)index tasks.

Lexical/BM25 is now the STORED ``chunks.text_tsv`` generated column (Phase 3),
maintained automatically on upsert — so the index tasks are no-ops kept only so
the manual /index endpoints stay valid, instantly-satisfied calls. These tests
pin that they run cleanly and touch neither Redis nor the vector store.
"""

from unittest.mock import MagicMock

from worker.tasks import index_collection, index_document


def test_index_document_task_is_noop(monkeypatch):
    fake_vs = MagicMock()
    fake_rds = MagicMock()
    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)

    index_document.apply(args=["doc1", "col1"]).get()

    fake_vs.assert_not_called()
    fake_rds.set.assert_not_called()


def test_index_collection_task_is_noop(monkeypatch):
    fake_vs = MagicMock()
    fake_rds = MagicMock()
    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)
    monkeypatch.setattr("worker.tasks._redis_singleton", fake_rds)

    index_collection.apply(args=["col1"]).get()

    fake_vs.assert_not_called()
    fake_rds.set.assert_not_called()
