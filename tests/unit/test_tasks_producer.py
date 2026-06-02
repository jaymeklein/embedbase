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


def test_task_name_constants():
    assert tp.INGEST_TASK == "worker.tasks.ingest_document"
    assert tp.DELETE_TASK == "worker.tasks.delete_document"
