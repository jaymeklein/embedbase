"""Unit tests for the authorization service (grants + hierarchy)."""

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, insert

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import users as users_t
from api.db import workspaces as ws_t
from api.services import permissions
from api.services.auth import Principal

_MASTER = Principal(is_master=True)
_USER = Principal(is_master=False, user_id="usr1")


async def _seed(db):
    """ws1 → {colA → docA, colB}; plus an active user usr1 with no grants."""
    await db.execute(
        insert(ws_t).values(
            id="ws1", name="W", description="", color="", icon="", created_at="t", updated_at="t"
        )
    )
    for cid in ("colA", "colB"):
        await db.execute(
            insert(col_t).values(
                id=cid, workspace_id="ws1", name=cid, description="", color="",
                icon="", created_at="t", updated_at="t",
            )
        )
    await db.execute(
        insert(doc_t).values(
            id="docA", collection_id="colA", filename="a.txt", file_type=".txt",
            created_at="t", updated_at="t",
        )
    )
    await db.execute(
        insert(users_t).values(
            id="usr1", email="u@e.com", name="", is_active=True, created_at="t", updated_at="t"
        )
    )
    await db.commit()


async def _grant(db, resource_type, resource_id, level, user_id="usr1"):
    return await permissions.grant_permission(db, user_id, resource_type, resource_id, level)


async def test_master_bypasses_every_check(db_session):
    await _seed(db_session)
    await permissions.authorize_collection(db_session, _MASTER, "colA", "write")
    await permissions.authorize_document(db_session, _MASTER, "docA", "write")


async def test_no_grant_is_denied(db_session):
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await permissions.authorize_collection(db_session, _USER, "colA", "read")
    assert exc.value.status_code == 403


async def test_collection_read_grant_allows_read_denies_write(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    await permissions.authorize_collection(db_session, _USER, "colA", "read")
    with pytest.raises(HTTPException):
        await permissions.authorize_collection(db_session, _USER, "colA", "write")


async def test_write_grant_implies_read(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "write")
    await permissions.authorize_collection(db_session, _USER, "colA", "read")
    await permissions.authorize_collection(db_session, _USER, "colA", "write")


async def test_workspace_grant_covers_collection_and_document(db_session):
    await _seed(db_session)
    await _grant(db_session, "workspace", "ws1", "read")
    await permissions.authorize_collection(db_session, _USER, "colA", "read")
    await permissions.authorize_document(db_session, _USER, "docA", "read")


async def test_collection_grant_covers_document(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "write")
    await permissions.authorize_document(db_session, _USER, "docA", "write")


async def test_document_grant_does_not_widen_to_collection(db_session):
    await _seed(db_session)
    await _grant(db_session, "document", "docA", "read")
    await permissions.authorize_document(db_session, _USER, "docA", "read")
    with pytest.raises(HTTPException):
        await permissions.authorize_collection(db_session, _USER, "colA", "read")


async def test_readable_collection_ids_filters_to_granted(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    got = await permissions.readable_collection_ids(db_session, _USER, ["colA", "colB"])
    assert got == ["colA"]


async def test_readable_collection_ids_master_sees_all(db_session):
    await _seed(db_session)
    got = await permissions.readable_collection_ids(db_session, _MASTER, ["colA", "colB"])
    assert got == ["colA", "colB"]


async def test_readable_collection_scope_master_is_unrestricted(db_session):
    await _seed(db_session)
    # None = "see everything"; callers skip filtering entirely.
    assert await permissions.readable_collection_scope(db_session, _MASTER) is None


async def test_readable_collection_scope_no_grants_is_empty(db_session):
    await _seed(db_session)
    assert await permissions.readable_collection_scope(db_session, _USER) == []


async def test_readable_collection_scope_collection_grant(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    assert await permissions.readable_collection_scope(db_session, _USER) == ["colA"]


async def test_readable_collection_scope_workspace_grant_expands(db_session):
    await _seed(db_session)
    await _grant(db_session, "workspace", "ws1", "read")
    got = await permissions.readable_collection_scope(db_session, _USER)
    assert set(got) == {"colA", "colB"}  # a workspace grant covers every collection under it


async def test_readable_collection_scope_excludes_document_only_grant(db_session):
    await _seed(db_session)
    await _grant(db_session, "document", "docA", "read")
    # A document-level grant doesn't make its collection browsable in these listings,
    # mirroring the collection documents listing (authorized on the collection).
    assert await permissions.readable_collection_scope(db_session, _USER) == []


async def test_filter_workspace_tree_prunes_and_recounts(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    tree = [
        {
            "id": "ws1", "name": "W", "collection_count": 2, "document_count": 1,
            "collections": [
                {"id": "colA", "name": "A", "document_count": 1},
                {"id": "colB", "name": "B", "document_count": 0},
            ],
        }
    ]
    filtered = await permissions.filter_workspace_tree(db_session, _USER, tree)
    assert len(filtered) == 1
    assert [c["id"] for c in filtered[0]["collections"]] == ["colA"]
    assert filtered[0]["collection_count"] == 1


async def test_grant_is_idempotent_and_relevels(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    await _grant(db_session, "collection", "colA", "write")
    grants = await permissions.list_permissions(db_session, "usr1")
    assert len(grants) == 1
    assert grants[0]["level"] == "write"


async def test_list_permissions_resolves_resource_names(db_session):
    await _seed(db_session)
    await _grant(db_session, "workspace", "ws1", "read")
    await _grant(db_session, "collection", "colA", "read")
    await _grant(db_session, "document", "docA", "read")
    names = {
        (g["resource_type"], g["resource_id"]): g["resource_name"]
        for g in await permissions.list_permissions(db_session, "usr1")
    }
    assert names[("workspace", "ws1")] == "W"
    assert names[("collection", "colA")] == "colA"
    assert names[("document", "docA")] == "a.txt"  # documents label by filename


async def test_list_permissions_name_is_none_for_deleted_resource(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    await db_session.execute(delete(col_t).where(col_t.c.id == "colA"))
    await db_session.commit()
    grants = await permissions.list_permissions(db_session, "usr1")
    assert grants[0]["resource_name"] is None  # dangling grant → no name


async def test_grant_on_missing_resource_raises_404(db_session):
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await permissions.grant_permission(db_session, "usr1", "collection", "col_ghost", "read")
    assert exc.value.status_code == 404


async def test_revoke_permission_removes_it(db_session):
    await _seed(db_session)
    grant = await _grant(db_session, "collection", "colA", "read")
    await permissions.revoke_permission(db_session, "usr1", grant["id"])
    assert await permissions.list_permissions(db_session, "usr1") == []


async def test_revoke_missing_permission_raises_404(db_session):
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await permissions.revoke_permission(db_session, "usr1", "perm_ghost")
    assert exc.value.status_code == 404
