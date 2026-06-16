"""Unit tests for the worker-side search bridge (D6 Phase 2b).

Covers effective-tag resolution (workspace → collection → document inheritance),
folding tags into chunks at ingestion, and the ``sync_document_tags`` task.
A real on-disk SQLite DB is used so the join queries run for real.
"""

from unittest.mock import MagicMock

from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from api.models.chunk import Chunk, ChunkMetadata
from api.tables import (
    collection_tags,
    collections,
    document_tags,
    documents,
    metadata,
    tags,
    workspace_tags,
    workspaces,
)
from worker.tasks import (
    _apply_effective_tags,
    _effective_document_tags,
    sync_document_tags,
)

_TS = "2026-01-01T00:00:00"


def _factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bridge.db'}", future=True, poolclass=NullPool
    )
    metadata.create_all(engine)
    return sessionmaker(engine, class_=Session, expire_on_commit=False)


def _seed(factory, *, ws_tags=(), col_tags=(), doc_tags=()) -> None:
    """Seed one workspace/collection/document and attach the given tag names."""
    with factory() as s:
        s.execute(insert(workspaces).values(
            id="ws_1", name="WS", created_at=_TS, updated_at=_TS))
        s.execute(insert(collections).values(
            id="col_1", workspace_id="ws_1", name="C", created_at=_TS, updated_at=_TS))
        s.execute(insert(documents).values(
            id="doc_1", collection_id="col_1", filename="f.txt", file_type="txt",
            created_at=_TS, updated_at=_TS))
        levels = [
            (ws_tags, workspace_tags, "workspace_id", "ws_1"),
            (col_tags, collection_tags, "collection_id", "col_1"),
            (doc_tags, document_tags, "document_id", "doc_1"),
        ]
        for names, join, col, entity_id in levels:
            for name in names:
                tag_id = f"tag_{name}"
                s.execute(insert(tags).values(
                    id=tag_id, workspace_id="ws_1", name=name, created_at=_TS))
                s.execute(insert(join).values(**{col: entity_id, "tag_id": tag_id}))
        s.commit()


def _chunk(idx: int) -> Chunk:
    return Chunk(
        text=f"chunk {idx}",
        metadata=ChunkMetadata(
            source_file="/f.txt", filename="f.txt", parser="txt",
            document_id="doc_1", chunk_index=idx,
        ),
    )


# --- _effective_document_tags ----------------------------------------------


def test_effective_tags_union_across_levels(tmp_path):
    factory = _factory(tmp_path)
    _seed(factory, ws_tags=["alpha"], col_tags=["beta"], doc_tags=["gamma"])
    with factory() as s:
        assert _effective_document_tags(s, "col_1", "doc_1") == ["alpha", "beta", "gamma"]


def test_effective_tags_deduplicated_and_sorted(tmp_path):
    factory = _factory(tmp_path)
    # same name "shared" at two levels must collapse to one entry.
    with factory() as s:
        s.execute(insert(workspaces).values(
            id="ws_1", name="WS", created_at=_TS, updated_at=_TS))
        s.execute(insert(collections).values(
            id="col_1", workspace_id="ws_1", name="C", created_at=_TS, updated_at=_TS))
        s.execute(insert(documents).values(
            id="doc_1", collection_id="col_1", filename="f.txt", file_type="txt",
            created_at=_TS, updated_at=_TS))
        s.execute(insert(tags).values(
            id="t1", workspace_id="ws_1", name="shared", created_at=_TS))
        s.execute(insert(tags).values(
            id="t2", workspace_id="ws_1", name="zeta", created_at=_TS))
        s.execute(insert(collection_tags).values(collection_id="col_1", tag_id="t1"))
        s.execute(insert(document_tags).values(document_id="doc_1", tag_id="t1"))
        s.execute(insert(document_tags).values(document_id="doc_1", tag_id="t2"))
        s.commit()
    with factory() as s:
        assert _effective_document_tags(s, "col_1", "doc_1") == ["shared", "zeta"]


def test_effective_tags_empty_when_untagged(tmp_path):
    factory = _factory(tmp_path)
    _seed(factory)
    with factory() as s:
        assert _effective_document_tags(s, "col_1", "doc_1") == []


def test_effective_tags_missing_collection_returns_empty(tmp_path):
    factory = _factory(tmp_path)
    with factory() as s:
        assert _effective_document_tags(s, "col_nope", "doc_nope") == []


# --- _apply_effective_tags --------------------------------------------------


def test_apply_effective_tags_folds_into_chunk_metadata(tmp_path):
    factory = _factory(tmp_path)
    _seed(factory, col_tags=["beta"], doc_tags=["gamma"])
    chunks = [_chunk(0), _chunk(1)]

    _apply_effective_tags(factory, "col_1", "doc_1", chunks)

    assert all(c.metadata.tags == ["beta", "gamma"] for c in chunks)


# --- sync_document_tags task ------------------------------------------------


def test_sync_task_writes_effective_tags_to_store(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed(factory, ws_tags=["alpha"], col_tags=["beta"])
    fake_vs = MagicMock()
    monkeypatch.setattr("worker.tasks.SessionLocal", factory)
    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)

    result = sync_document_tags.apply(args=["doc_1", "col_1"])

    assert result.successful()
    assert result.result is None  # pure command — no domain value returned
    fake_vs.set_document_tags.assert_called_once_with("col_1", "doc_1", ["alpha", "beta"])


def test_sync_task_has_retry_config():
    assert sync_document_tags.max_retries == 3
    assert sync_document_tags.retry_backoff is True


def test_sync_task_retries_on_store_error(tmp_path, monkeypatch):
    factory = _factory(tmp_path)
    _seed(factory, doc_tags=["x"])
    fake_vs = MagicMock()
    fake_vs.set_document_tags.side_effect = RuntimeError("store down")
    monkeypatch.setattr("worker.tasks.SessionLocal", factory)
    monkeypatch.setattr("worker.tasks._vector_store_singleton", fake_vs)

    result = sync_document_tags.apply(args=["doc_1", "col_1"])

    assert result.failed()
    assert fake_vs.set_document_tags.call_count == 4  # 1 + 3 retries
