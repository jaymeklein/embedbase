"""Unit tests for the tag service (api/services/tags.py).

Exercises the service directly against an in-memory session so document-level
tagging can be tested without the ingestion pipeline.
"""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select

from api.db import collection_tags, document_tags, workspace_tags
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import tags as tag_t
from api.db import workspaces as ws_t
from api.schemas.tags import TagMerge, TagUpdate
from api.services import tags as svc
from api.services import tasks as task_producer


@pytest.fixture
def sync_calls(monkeypatch):
    """Record search-bridge syncs (overrides the conftest no-op stub)."""
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        task_producer, "enqueue_sync_tags",
        lambda doc_id, col_id: recorded.append((doc_id, col_id)),
    )
    return recorded


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _mk_ws(db, ws_id="ws_1", name="WS"):
    await db.execute(
        insert(ws_t).values(
            id=ws_id, name=name, created_at=_now(), updated_at=_now()
        )
    )
    await db.commit()
    return ws_id


async def _mk_col(db, ws_id, col_id="col_1", name="Col"):
    await db.execute(
        insert(col_t).values(
            id=col_id, workspace_id=ws_id, name=name, created_at=_now(), updated_at=_now()
        )
    )
    await db.commit()
    return col_id


async def _mk_doc(db, col_id, doc_id="doc_1", filename="f.txt", status=None):
    await db.execute(
        insert(doc_t).values(
            id=doc_id, collection_id=col_id, filename=filename, file_type=".txt",
            created_at=_now(), updated_at=_now(), status=status,
        )
    )
    await db.commit()
    return doc_id


# ── normalize_tag ─────────────────────────────────────────────────────────────

def test_normalize_collapses_lowercases_trims():
    assert svc.normalize_tag("  Foo   BAR ") == "foo bar"


def test_normalize_empty_raises_422():
    with pytest.raises(HTTPException) as exc:
        svc.normalize_tag("   ")
    assert exc.value.status_code == 422


# ── create / list ─────────────────────────────────────────────────────────────

async def test_create_tag_returns_zero_counts(db_session):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "Python", "#fff", db_session)
    assert tag["name"] == "python"
    assert tag["color"] == "#fff"
    assert tag["workspace_count"] == 0
    assert tag["collection_count"] == 0
    assert tag["document_count"] == 0
    assert tag["id"].startswith("tag_")


async def test_create_tag_duplicate_returns_409(db_session):
    ws = await _mk_ws(db_session)
    await svc.create_tag(ws, "dup", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.create_tag(ws, "  DUP ", None, db_session)
    assert exc.value.status_code == 409


async def test_create_tag_workspace_not_found_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await svc.create_tag("ws_missing", "x", None, db_session)
    assert exc.value.status_code == 404


async def test_list_tags_includes_usage_counts(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    doc = await _mk_doc(db_session, col)
    tag = await svc.create_tag(ws, "shared", None, db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)
    await svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    await svc.assign_workspace_tag(ws, tag["id"], db_session)

    rows = await svc.list_tags(ws, db_session)
    assert len(rows) == 1
    assert rows[0]["collection_count"] == 1
    assert rows[0]["document_count"] == 1
    assert rows[0]["workspace_count"] == 1


async def test_list_tags_workspace_not_found_404(db_session):
    with pytest.raises(HTTPException) as exc:
        await svc.list_tags("ws_missing", db_session)
    assert exc.value.status_code == 404


# ── update ────────────────────────────────────────────────────────────────────

async def test_update_tag_renames_and_normalizes(db_session):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "old", None, db_session)
    out = await svc.update_tag(ws, tag["id"], TagUpdate(name="  New Name "), db_session)
    assert out["name"] == "new name"


async def test_update_tag_color_only(db_session):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "t", None, db_session)
    out = await svc.update_tag(ws, tag["id"], TagUpdate(color="#abc"), db_session)
    assert out["color"] == "#abc"
    assert out["name"] == "t"


async def test_update_tag_duplicate_name_409(db_session):
    ws = await _mk_ws(db_session)
    await svc.create_tag(ws, "a", None, db_session)
    b = await svc.create_tag(ws, "b", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.update_tag(ws, b["id"], TagUpdate(name="a"), db_session)
    assert exc.value.status_code == 409


async def test_update_tag_empty_body_returns_current(db_session):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "keep", None, db_session)
    out = await svc.update_tag(ws, tag["id"], TagUpdate(), db_session)
    assert out["name"] == "keep"


async def test_update_tag_not_found_404(db_session):
    ws = await _mk_ws(db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.update_tag(ws, "tag_missing", TagUpdate(name="x"), db_session)
    assert exc.value.status_code == 404


# ── delete + cascade ──────────────────────────────────────────────────────────

async def test_delete_tag_cascades_assignments(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    tag = await svc.create_tag(ws, "gone", None, db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)

    await svc.delete_tag(ws, tag["id"], db_session)
    remaining = (
        await db_session.execute(
            select(collection_tags).where(collection_tags.c.tag_id == tag["id"])
        )
    ).fetchall()
    assert remaining == []


async def test_delete_tag_not_found_404(db_session):
    ws = await _mk_ws(db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.delete_tag(ws, "tag_missing", db_session)
    assert exc.value.status_code == 404


# ── assignment ────────────────────────────────────────────────────────────────

async def test_assign_collection_tag_is_idempotent(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)
    rows = (
        await db_session.execute(
            select(collection_tags).where(collection_tags.c.collection_id == col)
        )
    ).fetchall()
    assert len(rows) == 1


async def test_unassign_collection_tag_removes_row(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)
    await svc.unassign_collection_tag(ws, col, tag["id"], db_session)
    rows = (
        await db_session.execute(
            select(collection_tags).where(collection_tags.c.collection_id == col)
        )
    ).fetchall()
    assert rows == []


async def test_unassign_when_not_assigned_is_noop(db_session):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.unassign_workspace_tag(ws, tag["id"], db_session)  # no raise


async def test_assign_workspace_tag(db_session):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_workspace_tag(ws, tag["id"], db_session)
    rows = (
        await db_session.execute(
            select(workspace_tags).where(workspace_tags.c.workspace_id == ws)
        )
    ).fetchall()
    assert len(rows) == 1


async def test_assign_document_tag(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    doc = await _mk_doc(db_session, col)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    await svc.unassign_document_tag(ws, col, doc, tag["id"], db_session)
    rows = (
        await db_session.execute(
            select(document_tags).where(document_tags.c.document_id == doc)
        )
    ).fetchall()
    assert rows == []


async def test_assign_tag_wrong_workspace_404(db_session):
    ws1 = await _mk_ws(db_session, "ws_1")
    ws2 = await _mk_ws(db_session, "ws_2")
    col = await _mk_col(db_session, ws1)
    tag = await svc.create_tag(ws2, "t", None, db_session)  # tag in the other workspace
    with pytest.raises(HTTPException) as exc:
        await svc.assign_collection_tag(ws1, col, tag["id"], db_session)
    assert exc.value.status_code == 404


async def test_assign_document_unknown_doc_404(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    tag = await svc.create_tag(ws, "t", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.assign_document_tag(ws, col, "doc_missing", tag["id"], db_session)
    assert exc.value.status_code == 404


# ── search-bridge syncs on mutation ───────────────────────────────────────────

async def test_assign_document_tag_syncs_that_document(db_session, sync_calls):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    doc = await _mk_doc(db_session, col)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    assert sync_calls == [(doc, col)]


async def test_assign_collection_tag_syncs_each_active_doc(db_session, sync_calls):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    await _mk_doc(db_session, col, "doc_a")
    await _mk_doc(db_session, col, "doc_b")
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)
    assert sorted(sync_calls) == [("doc_a", col), ("doc_b", col)]


async def test_rename_tag_syncs_workspace(db_session, sync_calls):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    doc = await _mk_doc(db_session, col)
    tag = await svc.create_tag(ws, "old", None, db_session)
    await svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    sync_calls.clear()
    await svc.update_tag(ws, tag["id"], TagUpdate(name="new"), db_session)
    assert (doc, col) in sync_calls


async def test_recolor_tag_does_not_sync(db_session, sync_calls):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.update_tag(ws, tag["id"], TagUpdate(color="#abc"), db_session)
    assert sync_calls == []


async def test_delete_tag_syncs_workspace(db_session, sync_calls):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    doc = await _mk_doc(db_session, col)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    sync_calls.clear()
    await svc.delete_tag(ws, tag["id"], db_session)
    assert (doc, col) in sync_calls


# ── merge ─────────────────────────────────────────────────────────────────────

async def test_merge_repoints_and_deletes_source(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    src = await svc.create_tag(ws, "src", None, db_session)
    dst = await svc.create_tag(ws, "dst", None, db_session)
    await svc.assign_collection_tag(ws, col, src["id"], db_session)

    out = await svc.merge_tags(ws, TagMerge(source_id=src["id"], target_id=dst["id"]), db_session)
    assert out["id"] == dst["id"]
    assert out["collection_count"] == 1
    # source gone
    assert (
        await db_session.execute(select(tag_t).where(tag_t.c.id == src["id"]))
    ).fetchone() is None


async def test_merge_deduplicates_shared_entity(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    src = await svc.create_tag(ws, "src", None, db_session)
    dst = await svc.create_tag(ws, "dst", None, db_session)
    await svc.assign_collection_tag(ws, col, src["id"], db_session)
    await svc.assign_collection_tag(ws, col, dst["id"], db_session)

    out = await svc.merge_tags(ws, TagMerge(source_id=src["id"], target_id=dst["id"]), db_session)
    assert out["collection_count"] == 1  # not double-counted


async def test_merge_into_self_422(db_session):
    ws = await _mk_ws(db_session)
    tag = await svc.create_tag(ws, "t", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.merge_tags(ws, TagMerge(source_id=tag["id"], target_id=tag["id"]), db_session)
    assert exc.value.status_code == 422


async def test_merge_source_missing_404(db_session):
    ws = await _mk_ws(db_session)
    dst = await svc.create_tag(ws, "dst", None, db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.merge_tags(ws, TagMerge(source_id="tag_x", target_id=dst["id"]), db_session)
    assert exc.value.status_code == 404


# ── correlation: tag_items ────────────────────────────────────────────────────

async def test_tag_items_lists_collections_and_documents(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    doc = await _mk_doc(db_session, col)
    deleted = await _mk_doc(db_session, col, doc_id="doc_2", filename="x.txt", status="deleting")
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)
    await svc.assign_document_tag(ws, col, doc, tag["id"], db_session)
    # tag the soft-deleted doc directly — it must be excluded from items
    await db_session.execute(
        insert(document_tags).values(document_id=deleted, tag_id=tag["id"])
    )
    await db_session.commit()

    items = await svc.tag_items(ws, tag["id"], db_session)
    assert [c["id"] for c in items["collections"]] == [col]
    assert [d["id"] for d in items["documents"]] == [doc]


async def test_tag_items_not_found_404(db_session):
    ws = await _mk_ws(db_session)
    with pytest.raises(HTTPException) as exc:
        await svc.tag_items(ws, "tag_missing", db_session)
    assert exc.value.status_code == 404


# ── filtering helpers ─────────────────────────────────────────────────────────

async def test_matching_entity_ids_requires_all_tags(db_session):
    ws = await _mk_ws(db_session)
    c1 = await _mk_col(db_session, ws, "col_1", "C1")
    c2 = await _mk_col(db_session, ws, "col_2", "C2")
    a = await svc.create_tag(ws, "a", None, db_session)
    b = await svc.create_tag(ws, "b", None, db_session)
    await svc.assign_collection_tag(ws, c1, a["id"], db_session)
    await svc.assign_collection_tag(ws, c1, b["id"], db_session)
    await svc.assign_collection_tag(ws, c2, a["id"], db_session)

    both = await svc.matching_entity_ids("collection", ["a", "b"], db_session)
    assert both == [c1]
    just_a = set(await svc.matching_entity_ids("collection", ["a"], db_session))
    assert just_a == {c1, c2}


async def test_tags_by_entity_groups_and_empty(db_session):
    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    tag = await svc.create_tag(ws, "t", None, db_session)
    await svc.assign_collection_tag(ws, col, tag["id"], db_session)

    mapping = await svc.tags_by_entity("collection", [col], db_session)
    assert mapping[col][0]["name"] == "t"
    assert await svc.tags_by_entity("collection", [], db_session) == {}


# ── list_collections / list_documents integration with tags ───────────────────

async def test_list_collections_filters_and_echoes_tags(db_session):
    from api.services.collections import list_collections

    ws = await _mk_ws(db_session)
    c1 = await _mk_col(db_session, ws, "col_1", "C1")
    await _mk_col(db_session, ws, "col_2", "C2")
    tag = await svc.create_tag(ws, "keep", None, db_session)
    await svc.assign_collection_tag(ws, c1, tag["id"], db_session)

    all_cols = await list_collections(ws, db_session)
    tagged = next(c for c in all_cols if c["id"] == c1)
    assert tagged["tags"][0]["name"] == "keep"

    filtered = await list_collections(ws, db_session, tags=["keep"])
    assert [c["id"] for c in filtered] == [c1]


async def test_list_documents_filters_and_echoes_tags(db_session):
    from api.services.documents import list_documents

    ws = await _mk_ws(db_session)
    col = await _mk_col(db_session, ws)
    d1 = await _mk_doc(db_session, col, "doc_1", "a.txt")
    await _mk_doc(db_session, col, "doc_2", "b.txt")
    tag = await svc.create_tag(ws, "keep", None, db_session)
    await svc.assign_document_tag(ws, col, d1, tag["id"], db_session)

    docs = await list_documents(db_session, col)
    tagged = next(d for d in docs if d["document_id"] == d1)
    assert tagged["tags"][0]["name"] == "keep"

    filtered = await list_documents(db_session, col, tags=["keep"])
    assert [d["document_id"] for d in filtered] == [d1]
