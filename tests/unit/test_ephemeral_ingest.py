"""Unit tests for temporary (ephemeral) ingestion + the purge sweep (PR 4).

Two halves, both infra-free:

* Service (async, ``db_session``): ``ingest(temporary=True)`` stamps ``expires_at``
  only when ``storage.temp_retention_hours > 0``; a normal upload never does.
* Worker (sync, throwaway SQLite): ``purge_expired_documents`` enqueues a delete for
  each *due* expired document and leaves future / permanent rows alone.
"""

import io
from datetime import UTC, datetime, timedelta

from fastapi import UploadFile
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import worker.tasks as wt
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import workspaces as ws_t
from api.models.config import AppConfig, StorageConfig
from api.services import documents as doc_svc
from api.services.auth import Principal
from api.tables import documents, metadata

_NOW = "2024-01-01T00:00:00"


# ── Service: expiry stamping ──────────────────────────────────────────────────


class _SpyStorage:
    async def put_upload(self, file, key, *, max_bytes):
        return 5


async def _seed_ws_col(db_session) -> None:
    await db_session.execute(
        insert(ws_t).values(id="ws1", name="WS", description="", color="", icon="", created_at=_NOW, updated_at=_NOW)
    )
    await db_session.execute(
        insert(col_t).values(id="col1", workspace_id="ws1", name="Col", description="", color="", icon="", created_at=_NOW, updated_at=_NOW)
    )
    await db_session.commit()


async def _ingest(db_session, monkeypatch, *, hours: int, temporary: bool):
    """Ingest one upload under a given retention config; return its stored expires_at."""
    await _seed_ws_col(db_session)
    monkeypatch.setattr(doc_svc, "get_storage", lambda cfg, name=None: _SpyStorage())
    monkeypatch.setattr(
        doc_svc, "get_app_config", lambda: AppConfig(storage=StorageConfig(temp_retention_hours=hours))
    )
    monkeypatch.setattr(doc_svc.task_producer, "enqueue_ingest", lambda *a: None)

    upload = UploadFile(filename="note.txt", file=io.BytesIO(b"hello"))
    result = await doc_svc.ingest(db_session, "col1", upload, Principal(is_master=True), temporary=temporary)
    return (
        await db_session.execute(select(doc_t.c.expires_at).where(doc_t.c.id == result["document_id"]))
    ).scalar()


async def test_temporary_upload_stamps_expiry(db_session, monkeypatch):
    before = datetime.now(UTC).replace(tzinfo=None)
    expires_at = await _ingest(db_session, monkeypatch, hours=24, temporary=True)
    after = datetime.now(UTC).replace(tzinfo=None)
    assert expires_at is not None
    # Stamped at now+24h, where `now` fell between `before` and `after`.
    assert before + timedelta(hours=24) <= expires_at <= after + timedelta(hours=24)


async def test_normal_upload_has_no_expiry(db_session, monkeypatch):
    # temporary flag off → permanent, even with retention configured.
    assert await _ingest(db_session, monkeypatch, hours=24, temporary=False) is None


async def test_temporary_is_noop_when_retention_disabled(db_session, monkeypatch):
    # temp_retention_hours=0 is the default-off switch: a temporary upload stays permanent.
    assert await _ingest(db_session, monkeypatch, hours=0, temporary=True) is None


async def test_temporary_local_path_stamps_expiry(db_session, monkeypatch, tmp_path):
    """The MCP ingest path (ingest_local_path) honours `temporary` too → stamps expires_at."""
    await _seed_ws_col(db_session)

    class _PathStorage:
        def put_path(self, src, key):  # ingest_local_path copies the on-disk file in
            return None

    monkeypatch.setattr(doc_svc, "get_storage", lambda cfg, name=None: _PathStorage())
    monkeypatch.setattr(
        doc_svc, "get_app_config",
        lambda: AppConfig(storage=StorageConfig(temp_retention_hours=24)),
    )
    monkeypatch.setattr(doc_svc.task_producer, "enqueue_ingest", lambda *a: None)

    src = tmp_path / "note.txt"
    src.write_text("hello")
    result = await doc_svc.ingest_local_path(
        db_session, "col1", str(src), Principal(is_master=True), temporary=True
    )
    expires_at = (
        await db_session.execute(
            select(doc_t.c.expires_at).where(doc_t.c.id == result["document_id"])
        )
    ).scalar()
    assert expires_at is not None


def test_documents_expires_at_column_is_nullable():
    # Migration/model guard: the column exists and permits NULL (= permanent).
    assert doc_t.c.expires_at.nullable is True


# ── Worker: purge sweep ───────────────────────────────────────────────────────


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'purge.db'}", future=True, poolclass=NullPool)
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed_doc(factory, *, doc_id, col_id, expires_at) -> None:
    with factory() as s:
        s.execute(
            insert(documents).values(
                id=doc_id, collection_id=col_id, filename="f.txt", file_type=".txt",
                expires_at=expires_at, created_at="t", updated_at="t",
            )
        )
        s.commit()


def test_purge_enqueues_only_due_expired_documents(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    now = datetime.now(UTC).replace(tzinfo=None)
    _seed_doc(factory, doc_id="doc_due", col_id="c1", expires_at=now - timedelta(hours=1))    # due
    _seed_doc(factory, doc_id="doc_future", col_id="c1", expires_at=now + timedelta(hours=1))  # not yet
    _seed_doc(factory, doc_id="doc_perm", col_id="c1", expires_at=None)                        # permanent

    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(wt, "SessionLocal", factory)
    monkeypatch.setattr(wt.delete_document, "delay", lambda doc_id, col_id: enqueued.append((doc_id, col_id)))

    count = wt.purge_expired_documents()

    assert count == 1
    assert enqueued == [("doc_due", "c1")]  # only the past-due row routes to delete


def test_purge_noop_when_nothing_expired(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed_doc(factory, doc_id="doc_perm", col_id="c1", expires_at=None)

    enqueued: list[tuple[str, str]] = []
    monkeypatch.setattr(wt, "SessionLocal", factory)
    monkeypatch.setattr(wt.delete_document, "delay", lambda doc_id, col_id: enqueued.append((doc_id, col_id)))

    assert wt.purge_expired_documents() == 0
    assert enqueued == []
