"""Unit tests for the API-side Celery producer (dispatch by task name)."""

from api.services import tasks as tp


class FakeResult:
    id = "task-123"


def test_enqueue_ingest_sends_named_task(monkeypatch):
    captured = {}

    def fake_send(name, args=None):
        captured["name"] = name
        captured["args"] = args
        return FakeResult()

    monkeypatch.setattr(tp._producer, "send_task", fake_send)

    task_id = tp.enqueue_ingest("job1", "/p/f.txt", "col1", "doc1", ".txt")
    assert task_id == "task-123"
    assert captured["name"] == "worker.tasks.ingest_document"
    assert captured["args"] == ["job1", "/p/f.txt", "col1", "doc1", ".txt"]


def test_enqueue_delete_sends_named_task(monkeypatch):
    captured = {}

    def fake_send(name, args=None):
        captured["name"] = name
        captured["args"] = args
        return FakeResult()

    monkeypatch.setattr(tp._producer, "send_task", fake_send)

    task_id = tp.enqueue_delete("doc1", "col1")
    assert task_id == "task-123"
    assert captured["name"] == "worker.tasks.delete_document"
    assert captured["args"] == ["doc1", "col1"]


def test_enqueue_sync_tags_sends_named_task(monkeypatch):
    captured = {}

    def fake_send(name, args=None):
        captured["name"] = name
        captured["args"] = args
        return FakeResult()

    monkeypatch.setattr(tp._producer, "send_task", fake_send)

    task_id = tp.enqueue_sync_tags("doc1", "col1")
    assert task_id == "task-123"
    assert captured["name"] == "worker.tasks.sync_document_tags"
    assert captured["args"] == ["doc1", "col1"]


def test_enqueue_index_document_sends_named_task(monkeypatch):
    captured = {}

    def fake_send(name, args=None):
        captured["name"] = name
        captured["args"] = args
        return FakeResult()

    monkeypatch.setattr(tp._producer, "send_task", fake_send)

    task_id = tp.enqueue_index_document("doc1", "col1")
    assert task_id == "task-123"
    assert captured["name"] == "worker.tasks.index_document"
    assert captured["args"] == ["doc1", "col1"]


def test_enqueue_index_collection_sends_named_task(monkeypatch):
    captured = {}

    def fake_send(name, args=None):
        captured["name"] = name
        captured["args"] = args
        return FakeResult()

    monkeypatch.setattr(tp._producer, "send_task", fake_send)

    task_id = tp.enqueue_index_collection("col1")
    assert task_id == "task-123"
    assert captured["name"] == "worker.tasks.index_collection"
    assert captured["args"] == ["col1"]


def test_task_name_constants():
    assert tp.INGEST_TASK == "worker.tasks.ingest_document"
    assert tp.DELETE_TASK == "worker.tasks.delete_document"
    assert tp.SYNC_TAGS_TASK == "worker.tasks.sync_document_tags"
    assert tp.INDEX_DOC_TASK == "worker.tasks.index_document"
    assert tp.INDEX_COLLECTION_TASK == "worker.tasks.index_collection"
