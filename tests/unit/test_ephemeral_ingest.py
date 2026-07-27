"""Unit tests for temporary (ephemeral) ingestion + the purge sweep (PR 4).

Two halves, both infra-free:

* Service (async, ``db_session``): ``ingest(retention_days=N)`` stamps ``expires_at``
  at ``now + N days``; an omitted (``None``) retention is permanent; a value outside
  ``[1, 30]`` is rejected (422).
* Worker (sync, throwaway SQLite): ``purge_expired_documents`` enqueues a delete for
  each *due* expired document and leaves future / permanent rows alone.
"""

import io
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import worker.tasks as wt
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import workspaces as ws_t
from api.models.config import AppConfig
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


async def _ingest(db_session, monkeypatch, *, retention_days):
    """Ingest one upload with a per-file retention; return its stored expires_at."""
    await _seed_ws_col(db_session)
    monkeypatch.setattr(doc_svc, "get_storage", lambda cfg, name=None: _SpyStorage())
    monkeypatch.setattr(doc_svc, "get_app_config", lambda: AppConfig())
    monkeypatch.setattr(doc_svc.task_producer, "enqueue_ingest", lambda *a: None)

    upload = UploadFile(filename="note.txt", file=io.BytesIO(b"hello"))
    result = await doc_svc.ingest(
        db_session, "col1", upload, Principal(is_master=True), retention_days=retention_days
    )
    return (
        await db_session.execute(select(doc_t.c.expires_at).where(doc_t.c.id == result["document_id"]))
    ).scalar()


async def test_retention_days_stamps_expiry(db_session, monkeypatch):
    before = datetime.now(UTC).replace(tzinfo=None)
    expires_at = await _ingest(db_session, monkeypatch, retention_days=7)
    after = datetime.now(UTC).replace(tzinfo=None)
    assert expires_at is not None
    # Stamped at now+7d, where `now` fell between `before` and `after`.
    assert before + timedelta(days=7) <= expires_at <= after + timedelta(days=7)


async def test_no_retention_is_permanent(db_session, monkeypatch):
    # Omitted retention (None) → permanent, no expires_at.
    assert await _ingest(db_session, monkeypatch, retention_days=None) is None


async def test_retention_days_out_of_range_rejected(db_session, monkeypatch):
    # 1-30 is the accepted band; below/above → 422 before any bytes are stored.
    await _seed_ws_col(db_session)
    monkeypatch.setattr(doc_svc, "get_storage", lambda cfg, name=None: _SpyStorage())
    monkeypatch.setattr(doc_svc, "get_app_config", lambda: AppConfig())
    monkeypatch.setattr(doc_svc.task_producer, "enqueue_ingest", lambda *a: None)
    for bad in (0, 31):
        upload = UploadFile(filename="note.txt", file=io.BytesIO(b"hello"))
        with pytest.raises(HTTPException) as exc:
            await doc_svc.ingest(
                db_session, "col1", upload, Principal(is_master=True), retention_days=bad
            )
        assert exc.value.status_code == 422


async def test_retention_days_local_path_stamps_expiry(db_session, monkeypatch, tmp_path):
    """The MCP ingest path (ingest_local_path) honours retention_days too → stamps expires_at."""
    await _seed_ws_col(db_session)

    class _PathStorage:
        def put_path(self, src, key):  # ingest_local_path copies the on-disk file in
            return None

    monkeypatch.setattr(doc_svc, "get_storage", lambda cfg, name=None: _PathStorage())
    monkeypatch.setattr(doc_svc, "get_app_config", lambda: AppConfig())
    monkeypatch.setattr(doc_svc.task_producer, "enqueue_ingest", lambda *a: None)

    src = tmp_path / "note.txt"
    src.write_text("hello")
    result = await doc_svc.ingest_local_path(
        db_session, "col1", str(src), Principal(is_master=True), retention_days=14
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


def _seed_doc(factory, *, doc_id, col_id, expires_at, status=None, created_at="t") -> None:
    with factory() as s:
        s.execute(
            insert(documents).values(
                id=doc_id, collection_id=col_id, filename="f.txt", file_type=".txt",
                expires_at=expires_at, status=status, created_at=created_at, updated_at="t",
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


def test_purge_reaps_stale_awaiting_upload(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    now = datetime.now(UTC)
    old = (now - timedelta(hours=48)).isoformat()   # older than the 24h reservation TTL
    fresh = now.isoformat()
    # A permanent (expires_at=None) reservation that was abandoned long ago is reaped; a
    # fresh reservation (still likely to be confirmed) is left alone.
    _seed_doc(
        factory, doc_id="stale", col_id="c1", expires_at=None,
        status="awaiting_upload", created_at=old,
    )
    _seed_doc(
        factory, doc_id="fresh", col_id="c1", expires_at=None,
        status="awaiting_upload", created_at=fresh,
    )

    enqueued: list[str] = []
    monkeypatch.setattr(wt, "SessionLocal", factory)
    monkeypatch.setattr(wt.delete_document, "delay", lambda doc_id, col_id: enqueued.append(doc_id))

    assert wt.purge_expired_documents() == 1
    assert enqueued == ["stale"]
