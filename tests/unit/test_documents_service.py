"""Unit tests for document service soft-delete logic."""

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.db import workspaces as ws_t
from api.services.auth import Principal
from api.services.documents import delete_document, get_document_file, list_documents

_NOW = "2024-01-01T00:00:00"
_WS_ID = "ws_del_test"
_COL_ID = "col_del_test"
_DOC_ID = "doc_del_test"


async def _insert_workspace(db_session) -> None:
    await db_session.execute(
        insert(ws_t).values(id=_WS_ID, name="WS", description="", color="", icon="", created_at=_NOW, updated_at=_NOW)
    )


async def _insert_collection(db_session) -> None:
    await db_session.execute(
        insert(col_t).values(id=_COL_ID, workspace_id=_WS_ID, name="Col", description="", color="", icon="", created_at=_NOW, updated_at=_NOW)
    )


async def _insert_document(db_session) -> None:
    await db_session.execute(
        insert(doc_t).values(id=_DOC_ID, collection_id=_COL_ID, filename="f.txt", file_type=".txt", file_size=100, chunk_count=None, created_at=_NOW, updated_at=_NOW, status=None)
    )


async def _insert_job(db_session) -> None:
    await db_session.execute(
        insert(job_t).values(job_id="job_del_test", document_id=_DOC_ID, collection_id=_COL_ID, filename="f.txt", file_type=".txt", status="done", created_at=_NOW, updated_at=_NOW)
    )


async def _seed(db_session) -> None:
    """Seed workspace, collection, document, and one job record."""
    await _insert_workspace(db_session)
    await _insert_collection(db_session)
    await _insert_document(db_session)
    await _insert_job(db_session)
    await db_session.commit()


async def test_delete_marks_status_as_deleting(db_session, monkeypatch) -> None:
    monkeypatch.setattr("api.services.documents.task_producer.enqueue_delete", lambda *_: None)
    await _seed(db_session)

    await delete_document(db_session, _COL_ID, _DOC_ID)

    row = (
        await db_session.execute(select(doc_t).where(doc_t.c.id == _DOC_ID))
    ).fetchone()
    assert row is not None
    assert row.status == "deleting"


async def test_delete_does_not_hard_delete_document_row(db_session, monkeypatch) -> None:
    monkeypatch.setattr("api.services.documents.task_producer.enqueue_delete", lambda *_: None)
    await _seed(db_session)

    await delete_document(db_session, _COL_ID, _DOC_ID)

    count = (
        await db_session.execute(select(doc_t).where(doc_t.c.id == _DOC_ID))
    ).fetchone()
    assert count is not None


async def test_delete_removes_job_records(db_session, monkeypatch) -> None:
    monkeypatch.setattr("api.services.documents.task_producer.enqueue_delete", lambda *_: None)
    await _seed(db_session)

    await delete_document(db_session, _COL_ID, _DOC_ID)

    jobs = (
        await db_session.execute(
            select(job_t).where(job_t.c.document_id == _DOC_ID)
        )
    ).fetchall()
    assert jobs == []


async def test_delete_already_deleting_raises_404(db_session, monkeypatch) -> None:
    monkeypatch.setattr("api.services.documents.task_producer.enqueue_delete", lambda *_: None)
    await _seed(db_session)

    await delete_document(db_session, _COL_ID, _DOC_ID)

    with pytest.raises(HTTPException) as exc:
        await delete_document(db_session, _COL_ID, _DOC_ID)
    assert exc.value.status_code == 404


async def test_delete_nonexistent_raises_404(db_session) -> None:
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await delete_document(db_session, _COL_ID, "doc_ghost")
    assert exc.value.status_code == 404


async def test_list_documents_excludes_deleting_status(db_session, monkeypatch) -> None:
    monkeypatch.setattr("api.services.documents.task_producer.enqueue_delete", lambda *_: None)
    await _seed(db_session)

    docs_before = await list_documents(db_session, _COL_ID)
    assert len(docs_before) == 1

    await delete_document(db_session, _COL_ID, _DOC_ID)

    docs_after = await list_documents(db_session, _COL_ID)
    assert docs_after == []


async def test_get_document_file_returns_path_and_name(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("api.services.documents.settings.upload_dir", str(tmp_path))
    await _seed(db_session)
    stored = tmp_path / _COL_ID / f"{_DOC_ID}.txt"
    stored.parent.mkdir(parents=True)
    stored.write_text("hello")

    path, filename = await get_document_file(db_session, _DOC_ID, Principal(is_master=True))

    assert path == stored
    assert filename == "f.txt"


async def test_get_document_file_missing_doc_raises_404(db_session) -> None:
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await get_document_file(db_session, "doc_ghost", Principal(is_master=True))
    assert exc.value.status_code == 404


async def test_get_document_file_missing_on_disk_raises_404(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("api.services.documents.settings.upload_dir", str(tmp_path))
    await _seed(db_session)  # row exists, but no file was written to disk
    with pytest.raises(HTTPException) as exc:
        await get_document_file(db_session, _DOC_ID, Principal(is_master=True))
    assert exc.value.status_code == 404


async def test_get_document_file_wrong_collection_raises_403(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("api.services.documents.settings.upload_dir", str(tmp_path))
    await _seed(db_session)
    principal = Principal(is_master=False, collection_id="col_other")
    with pytest.raises(HTTPException) as exc:
        await get_document_file(db_session, _DOC_ID, principal)
    assert exc.value.status_code == 403


async def test_delete_enqueue_failure_rolls_back_tombstone(db_session, monkeypatch) -> None:
    def raise_on_enqueue(*_) -> None:
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("api.services.documents.task_producer.enqueue_delete", raise_on_enqueue)
    await _seed(db_session)

    with pytest.raises(HTTPException) as exc:
        await delete_document(db_session, _COL_ID, _DOC_ID)
    assert exc.value.status_code == 503

    row = (await db_session.execute(select(doc_t).where(doc_t.c.id == _DOC_ID))).fetchone()
    assert row is not None
    assert row.status is None
