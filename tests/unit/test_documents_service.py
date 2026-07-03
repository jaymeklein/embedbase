"""Unit tests for document service soft-delete, download, and ingest wiring."""

import io

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import insert, select, update

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import job_records as job_t
from api.db import workspaces as ws_t
from api.services.auth import Principal
from api.services.documents import (
    delete_document,
    ingest,
    list_documents,
    resolve_document_download,
)

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


async def test_list_documents_dedupes_multiple_jobs(db_session) -> None:
    """A document with several job rows (re-ingest/retries) appears once, latest job."""
    await _seed(db_session)  # one job: status "done", created 2024-01-01
    await db_session.execute(
        insert(job_t).values(
            job_id="job_newer", document_id=_DOC_ID, collection_id=_COL_ID,
            filename="f.txt", file_type=".txt", status="failed",
            created_at="2024-02-01T00:00:00", updated_at="2024-02-01T00:00:00",
        )
    )
    await db_session.commit()

    docs = await list_documents(db_session, _COL_ID)
    assert len(docs) == 1
    assert docs[0]["status"] == "failed"  # latest job wins


async def test_list_documents_excludes_deleting_status(db_session, monkeypatch) -> None:
    monkeypatch.setattr("api.services.documents.task_producer.enqueue_delete", lambda *_: None)
    await _seed(db_session)

    docs_before = await list_documents(db_session, _COL_ID)
    assert len(docs_before) == 1

    await delete_document(db_session, _COL_ID, _DOC_ID)

    docs_after = await list_documents(db_session, _COL_ID)
    assert docs_after == []


class _FakeS3Storage:
    """Presigns like an S3 backend so resolve_document_download redirects."""

    def presigned_get(self, key: str, filename: str) -> str:
        return f"https://s3.example.com/{key}?sig=abc"

    def local_path(self, key: str):
        return None


async def test_resolve_download_local_returns_fileresponse(db_session, monkeypatch, tmp_path) -> None:
    # Default backend is local (storage_backend NULL) → serve the on-disk file inline.
    monkeypatch.setattr("api.services.storage.settings.upload_dir", str(tmp_path))
    await _seed(db_session)
    stored = tmp_path / _COL_ID / f"{_DOC_ID}.txt"
    stored.parent.mkdir(parents=True)
    stored.write_text("hello")

    resp = await resolve_document_download(db_session, _DOC_ID, Principal(is_master=True))

    assert isinstance(resp, FileResponse)
    assert str(resp.path) == str(stored)
    assert resp.filename == "f.txt"


async def test_resolve_download_s3_backend_returns_302_redirect(db_session, monkeypatch, tmp_path) -> None:
    await _seed(db_session)
    await db_session.execute(
        update(doc_t).where(doc_t.c.id == _DOC_ID).values(storage_backend="s3x")
    )
    await db_session.commit()
    monkeypatch.setattr(
        "api.services.documents.get_storage", lambda cfg, name=None: _FakeS3Storage()
    )

    resp = await resolve_document_download(db_session, _DOC_ID, Principal(is_master=True))

    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://s3.example.com/")


async def test_resolve_download_missing_doc_raises_404(db_session) -> None:
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await resolve_document_download(db_session, "doc_ghost", Principal(is_master=True))
    assert exc.value.status_code == 404


async def test_resolve_download_missing_on_disk_raises_404(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("api.services.storage.settings.upload_dir", str(tmp_path))
    await _seed(db_session)  # row exists, but no file was written to disk
    with pytest.raises(HTTPException) as exc:
        await resolve_document_download(db_session, _DOC_ID, Principal(is_master=True))
    assert exc.value.status_code == 404


async def test_resolve_download_wrong_collection_raises_403(db_session, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("api.services.storage.settings.upload_dir", str(tmp_path))
    await _seed(db_session)
    principal = Principal(is_master=False, collection_id="col_other")
    with pytest.raises(HTTPException) as exc:
        await resolve_document_download(db_session, _DOC_ID, principal)
    assert exc.value.status_code == 403


class _SpyStorage:
    """Records put_upload; returns a fixed byte count (no real backend)."""

    def __init__(self) -> None:
        self.put_keys: list[str] = []

    async def put_upload(self, file, key, *, max_bytes):
        self.put_keys.append(key)
        return 5


async def test_ingest_records_default_storage_backend(db_session, monkeypatch) -> None:
    await _insert_workspace(db_session)
    await _insert_collection(db_session)
    await db_session.commit()
    spy = _SpyStorage()
    monkeypatch.setattr("api.services.documents.get_storage", lambda cfg, name=None: spy)
    monkeypatch.setattr("api.services.documents.task_producer.enqueue_ingest", lambda *a: None)

    upload = UploadFile(filename="note.txt", file=io.BytesIO(b"hello"))
    result = await ingest(db_session, _COL_ID, upload, Principal(is_master=True))

    # Stored under the canonical key and the row records the default backend name.
    assert spy.put_keys == [f"{_COL_ID}/{result['document_id']}.txt"]
    row = (
        await db_session.execute(
            select(doc_t.c.storage_backend).where(doc_t.c.id == result["document_id"])
        )
    ).fetchone()
    assert row.storage_backend == "local"


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
