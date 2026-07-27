"""Unit tests for the authorization service (grants + hierarchy)."""

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, insert

from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import users as users_t
from api.db import workspaces as ws_t
from api.services import access, permissions
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


async def test_no_permissions_is_unrestricted(db_session):
    await _seed(db_session)
    # No permissions at all → the user reads AND writes everything (open default).
    await permissions.authorize_collection(db_session, _USER, "colA", "write")
    await permissions.authorize_document(db_session, _USER, "docA", "write")
    await permissions.authorize_workspace(db_session, _USER, "ws1", "read")


async def test_collection_grant_scopes_out_siblings_and_other_workspaces(db_session):
    await _seed(db_session)  # ws1 → {colA → docA, colB}
    await db_session.execute(
        insert(ws_t).values(
            id="ws2", name="W2", description="", color="", icon="", created_at="t", updated_at="t"
        )
    )
    await db_session.execute(
        insert(col_t).values(
            id="colC", workspace_id="ws2", name="colC", description="", color="",
            icon="", created_at="t", updated_at="t",
        )
    )
    await db_session.commit()
    await _grant(db_session, "collection", "colA", "read")
    # Scoped to colA: its sibling colB and the other workspace (colC) fall out of scope.
    await permissions.authorize_collection(db_session, _USER, "colA", "read")
    for cid in ("colB", "colC"):
        with pytest.raises(HTTPException):
            await permissions.authorize_collection(db_session, _USER, cid, "read")
    with pytest.raises(HTTPException):
        await permissions.authorize_workspace(db_session, _USER, "ws2", "read")


async def test_workspace_read_grant_is_view_only(db_session):
    await _seed(db_session)
    await _grant(db_session, "workspace", "ws1", "read")
    # Every collection is readable, but a read-level workspace permission caps writes.
    await permissions.authorize_collection(db_session, _USER, "colA", "read")
    await permissions.authorize_collection(db_session, _USER, "colB", "read")
    with pytest.raises(HTTPException):
        await permissions.authorize_collection(db_session, _USER, "colA", "write")
    with pytest.raises(HTTPException):
        await permissions.authorize_document(db_session, _USER, "docA", "write")


async def test_workspace_write_grant_allows_write(db_session):
    await _seed(db_session)
    await _grant(db_session, "workspace", "ws1", "write")
    await permissions.authorize_collection(db_session, _USER, "colA", "write")
    await permissions.authorize_document(db_session, _USER, "docA", "write")


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


async def test_document_grant_is_direct_access_only(db_session):
    await _seed(db_session)  # ws1 → {colA → docA, colB}
    await _grant(db_session, "document", "docA", "read")
    # The granted document is directly readable…
    await permissions.authorize_document(db_session, _USER, "docA", "read")
    # …but a document grant must NOT open its collection for browsing/search or writes (no
    # ingest into colA), and the sibling collection stays out of scope entirely.
    for need in ("read", "write"):
        with pytest.raises(HTTPException):
            await permissions.authorize_collection(db_session, _USER, "colA", need)
    with pytest.raises(HTTPException):
        await permissions.authorize_document(db_session, _USER, "docA", "write")
    with pytest.raises(HTTPException):
        await permissions.authorize_collection(db_session, _USER, "colB", "read")


async def test_document_grant_does_not_open_collection_for_browsing(db_session):
    await _seed(db_session)
    await _grant(db_session, "document", "docA", "read")
    # readable_collection_ids feeds /search + /collections; a document grant must not make the
    # parent collection sweepable (which would return sibling documents).
    assert await permissions.readable_collection_ids(db_session, _USER, ["colA", "colB"]) == []


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


async def test_readable_collection_scope_no_permissions_is_unrestricted(db_session):
    await _seed(db_session)
    # No permissions → None (unrestricted; callers skip filtering and see every collection).
    assert await permissions.readable_collection_scope(db_session, _USER) is None


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
    # A document grant is direct-access only — it must not surface its collection in the
    # collection-grained views (queue, indexing), which would leak sibling documents.
    assert await permissions.readable_collection_scope(db_session, _USER) == []


# ── Capabilities + resource creation ────────────────────────────────────────

async def test_workspace_creation_requires_capability(db_session):
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await permissions.authorize_workspace_creation(db_session, _USER)
    assert exc.value.status_code == 403
    await permissions.authorize_workspace_creation(db_session, _MASTER)  # master always may


async def test_capability_grant_allows_workspace_creation(db_session):
    await _seed(db_session)
    await _grant(db_session, "capability", permissions.CAP_CREATE_WORKSPACE, "write")
    await permissions.authorize_workspace_creation(db_session, _USER)  # no raise
    assert await permissions.has_capability(db_session, _USER, permissions.CAP_CREATE_WORKSPACE)


async def test_capability_grant_does_not_scope_data_access(db_session):
    await _seed(db_session)
    await _grant(db_session, "capability", permissions.CAP_CREATE_WORKSPACE, "write")
    # A capability is not a data grant — the user stays unrestricted for read/write.
    assert await permissions.readable_collection_scope(db_session, _USER) is None
    await permissions.authorize_collection(db_session, _USER, "colA", "write")


async def test_capability_grant_lists_with_friendly_name(db_session):
    await _seed(db_session)
    await _grant(db_session, "capability", permissions.CAP_CREATE_WORKSPACE, "write")
    grants = await permissions.list_permissions(db_session, "usr1")
    cap = next(g for g in grants if g["resource_type"] == "capability")
    assert cap["resource_name"] == "Create workspaces"


async def test_unknown_capability_grant_raises_422(db_session):
    await _seed(db_session)
    with pytest.raises(HTTPException) as exc:
        await permissions.grant_permission(db_session, "usr1", "capability", "bogus", "write")
    assert exc.value.status_code == 422


async def test_tag_management_requires_capability(db_session):
    await _seed(db_session)
    # A no-permission user is unrestricted for data but does NOT get the capability implicitly.
    with pytest.raises(HTTPException) as exc:
        await permissions.authorize_tag_management(db_session, _USER, "ws1")
    assert exc.value.status_code == 403
    await permissions.authorize_tag_management(db_session, _MASTER, "ws1")  # master always may


async def test_capability_grant_allows_tag_management(db_session):
    await _seed(db_session)
    await _grant(db_session, "capability", permissions.CAP_MANAGE_TAGS, "write")
    # Capability + (unrestricted → readable) workspace → allowed.
    await permissions.authorize_tag_management(db_session, _USER, "ws1")  # no raise
    assert await permissions.has_capability(db_session, _USER, permissions.CAP_MANAGE_TAGS)


async def test_tag_management_denied_outside_workspace_scope(db_session):
    await _seed(db_session)  # ws1 → {colA, colB}
    # A second workspace the user IS scoped to, leaving ws1 out of their read scope.
    await db_session.execute(
        insert(ws_t).values(
            id="ws2", name="W2", description="", color="", icon="", created_at="t", updated_at="t"
        )
    )
    await db_session.execute(
        insert(col_t).values(
            id="colC", workspace_id="ws2", name="colC", description="", color="",
            icon="", created_at="t", updated_at="t",
        )
    )
    await db_session.commit()
    await _grant(db_session, "collection", "colC", "read")  # scopes the user to ws2 only
    await _grant(db_session, "capability", permissions.CAP_MANAGE_TAGS, "write")
    # Holds the capability, but ws1 is outside their scope → cannot manage its tags.
    with pytest.raises(HTTPException) as exc:
        await permissions.authorize_tag_management(db_session, _USER, "ws1")
    assert exc.value.status_code == 403
    await permissions.authorize_tag_management(db_session, _USER, "ws2")  # own scope is fine


async def test_writable_workspace_ids(db_session):
    await _seed(db_session)
    assert await permissions.writable_workspace_ids(db_session, _MASTER, ["ws1"]) == ["ws1"]
    # No permissions → unrestricted → writable everywhere.
    assert await permissions.writable_workspace_ids(db_session, _USER, ["ws1"]) == ["ws1"]
    await _grant(db_session, "workspace", "ws1", "read")
    assert await permissions.writable_workspace_ids(db_session, _USER, ["ws1"]) == []  # read caps it


async def test_writable_collection_ids(db_session):
    await _seed(db_session)  # ws1 → {colA, colB}
    # Master and no-permission (unrestricted) users may write every candidate.
    assert await permissions.writable_collection_ids(
        db_session, _MASTER, ["colA", "colB"]
    ) == ["colA", "colB"]
    assert await permissions.writable_collection_ids(
        db_session, _USER, ["colA", "colB"]
    ) == ["colA", "colB"]
    # A read grant scopes but doesn't confer write; a write grant does.
    await _grant(db_session, "collection", "colA", "read")
    await _grant(db_session, "collection", "colB", "write")
    assert await permissions.writable_collection_ids(db_session, _USER, ["colA", "colB"]) == ["colB"]


async def test_writable_collection_ids_never_leaks_past_visibility(db_session):
    """Write never leaks past the read narrowing: a collection the user can't see is never
    writable, even when an ancestor workspace-write grant would otherwise cover it."""
    await _seed(db_session)
    await _grant(db_session, "workspace", "ws1", "write")
    await _grant(db_session, "collection", "colA", "read")  # narrows ws1; colA becomes read-only
    # colA is capped read-only and colB is narrowed out of scope → neither is writable.
    assert await permissions.writable_collection_ids(db_session, _USER, ["colA", "colB"]) == []


async def test_grant_creator_access_grants_scoped_user(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")  # makes usr1 a scoped user
    await permissions.grant_creator_access(db_session, _USER, "ws1")
    grants = await permissions.list_permissions(db_session, "usr1")
    ws_grant = next(
        (g for g in grants if g["resource_type"] == "workspace" and g["resource_id"] == "ws1"),
        None,
    )
    assert ws_grant is not None and ws_grant["level"] == "write"


async def test_grant_creator_access_skips_unrestricted_user(db_session):
    await _seed(db_session)
    # usr1 has no permissions → unrestricted → auto-granting would wrongly scope them down.
    await permissions.grant_creator_access(db_session, _USER, "ws1")
    assert await permissions.list_permissions(db_session, "usr1") == []


async def test_grant_creator_access_skips_capability_only_user(db_session):
    await _seed(db_session)
    await _grant(db_session, "capability", permissions.CAP_CREATE_WORKSPACE, "write")
    # A capability grant doesn't scope data → the user is still unrestricted → no auto-grant.
    await permissions.grant_creator_access(db_session, _USER, "ws1")
    grants = await permissions.list_permissions(db_session, "usr1")
    assert [g["resource_type"] for g in grants] == ["capability"]  # only the capability remains


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


# ── access.py policies: composable authz + existence, applied authorize-first ──
#
# Each policy's ``apply`` raises to deny / returns None to allow; CompositePolicy applies its
# policies in order. Authorization policies precede existence policies, so a scoped caller who
# may not reach a resource gets 403 before any 404 — the 404 can never be an existence oracle.


async def test_authorize_collection_policy_denies_out_of_scope(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")  # scoped to colA
    await access.AuthorizeCollection("colA", "read").apply(db_session, _USER)  # in scope → allow
    with pytest.raises(HTTPException) as exc:  # a sibling is out of scope
        await access.AuthorizeCollection("colB", "read").apply(db_session, _USER)
    assert exc.value.status_code == 403


async def test_authorize_collection_policy_read_grant_denies_write(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    with pytest.raises(HTTPException) as exc:  # a read grant never confers write
        await access.AuthorizeCollection("colA", "write").apply(db_session, _USER)
    assert exc.value.status_code == 403


async def test_authorize_workspace_policy_denies_out_of_scope(db_session):
    await _seed(db_session)
    await _grant(db_session, "workspace", "ws1", "read")  # scoped to ws1
    await access.AuthorizeWorkspace("ws1", "read").apply(db_session, _USER)  # allow
    with pytest.raises(HTTPException) as exc:
        await access.AuthorizeWorkspace("ws_missing", "read").apply(db_session, _USER)
    assert exc.value.status_code == 403


async def test_authorize_document_policy_allows_granted(db_session):
    await _seed(db_session)
    await _grant(db_session, "document", "docA", "read")
    assert await access.AuthorizeDocument("docA", "read").apply(db_session, _USER) is None


async def test_collection_in_workspace_policy_404_when_absent(db_session):
    await _seed(db_session)
    await access.CollectionInWorkspace("ws1", "colA").apply(db_session, _MASTER)  # present → allow
    with pytest.raises(HTTPException) as exc:
        await access.CollectionInWorkspace("ws1", "col_missing").apply(db_session, _MASTER)
    assert exc.value.status_code == 404


async def test_composite_applies_authorization_before_existence(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")  # scoped
    # Missing collection: the authorize policy (403) fires before the existence policy (404),
    # so a scoped caller gets 403 — the 404 can never be an existence oracle.
    policy = access.CompositePolicy(
        access.AuthorizeCollection("col_missing", "read"),
        access.CollectionInWorkspace("ws1", "col_missing"),
    )
    with pytest.raises(HTTPException) as exc:
        await policy.apply(db_session, _USER)
    assert exc.value.status_code == 403


async def test_composite_reaches_existence_only_after_authorization(db_session):
    await _seed(db_session)  # _USER unrestricted → authorize passes
    # An authorized caller reaches the existence policy → 404 for a genuinely missing collection.
    policy = access.CompositePolicy(
        access.AuthorizeCollection("col_missing", "read"),
        access.CollectionInWorkspace("ws1", "col_missing"),
    )
    with pytest.raises(HTTPException) as exc:
        await policy.apply(db_session, _USER)
    assert exc.value.status_code == 404


async def test_composite_passes_when_all_policies_allow(db_session):
    await _seed(db_session)
    await _grant(db_session, "collection", "colA", "read")
    policy = access.CompositePolicy(
        access.AuthorizeCollection("colA", "read"),
        access.CollectionInWorkspace("ws1", "colA"),
    )
    assert await policy.apply(db_session, _USER) is None  # every policy allows → no raise


async def test_composite_document_path_authorizes_before_existence(db_session):
    await _seed(db_session)
    await _grant(db_session, "document", "docA", "read")  # scoped to docA only
    # Unauthorized document + a bad collection path → 403 (document authz first), never 404.
    policy = access.CompositePolicy(
        access.AuthorizeDocument("doc_other", "read"),
        access.CollectionInWorkspace("ws1", "col_missing"),
    )
    with pytest.raises(HTTPException) as exc:
        await policy.apply(db_session, _USER)
    assert exc.value.status_code == 403


def test_composite_rejects_empty_fail_closed():
    # An empty composite would authorize everything (empty loop) — reject it at construction.
    with pytest.raises(ValueError):
        access.CompositePolicy()
