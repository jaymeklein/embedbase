"""MCP tool implementations.

Each function is a thin, framework-agnostic wrapper over an existing service, so
the same logic is exercised by both the ``FastMCP`` server
(:mod:`api.services.mcp.server`) and the integration tests — no transport is
needed to test a tool. The server layer resolves the
``db``/``embedder``/``vector_store``/``principal`` dependencies and passes them in
as keyword arguments.

Every tool receives the authenticated ``principal`` and enforces that caller's
grants (via :mod:`api.services.permissions`) before touching data: reads require a
``read`` grant on the collection (or an ancestor), writes require ``write``, and
``list_workspaces`` is filtered to what the caller may read. The master principal
passes every check.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, Reranker
from api.adapters.vector_store.pgvector import PgvectorAdapter, chunk_index_order_key
from api.constants import DEFAULT_EXPAND_CHAR_BUDGET
from api.models.document import DocumentListQuery, JobListQuery
from api.models.search import SearchRequest, SearchResponse, SearchResult
from api.schemas.collections import CollectionUpdate
from api.schemas.tags import TagMerge, TagUpdate
from api.schemas.workspaces import WorkspaceUpdate
from api.services import collections as collection_svc
from api.services import documents as doc_svc
from api.services import jobs as jobs_svc
from api.services import permissions
from api.services import tags as tag_svc
from api.services import workspaces as ws_svc
from api.services.auth import Principal
from api.services.search import multi_collection_search

_TOP_K_FLOOR = 1


def _saturation_notice(
    response: SearchResponse, max_results: int, ranked_shown: int
) -> str | None:
    """A natural-language "there's more" hint for the model when relevant chunks fell below the
    ``top_k`` cut (plan A3 — the MCP must tell the caller it returned only part of what matched).

    Returns ``None`` when results look complete (``more_available`` is false), so the warning
    stays meaningful and the model doesn't learn to ignore it. The LLM reads this text, not the
    structured ``more_available`` field, so it is phrased as an actionable instruction.

    ``ranked_shown`` is the number of ranked hits shown *before* A2 expansion coalesces them into
    spans. The notice describes the **ranking** cut (what a higher ``top_k`` changes), so its counts
    must stay on the pre-expansion scale that ``more_available`` was measured on — not the
    possibly-fewer merged spans in ``response.results``.
    """
    if not response.more_available:
        return None
    matched = sum(s.returned_after_filter for s in response.collection_stats.values())
    return (
        f"{matched} chunks matched and were ranked; showing the top {ranked_shown}. About "
        f"{matched - ranked_shown} more, of comparable relevance, fell below the top_k cut. If the "
        f"answer seems incomplete, re-run search_documents with a higher top_k "
        f"(up to {max_results})."
    )


async def list_workspaces(*, db: AsyncSession, principal: Principal) -> dict[str, Any]:
    """Return the workspace tree, filtered to what ``principal`` may read."""
    tree = await ws_svc.list_workspace_tree(db)
    tree = await permissions.filter_workspace_tree(db, principal, tree)
    return {"workspaces": tree}


async def search_documents(
    *,
    query: str,
    collection_ids: list[str],
    top_k: int = 5,
    hybrid: bool = True,
    filters: dict[str, Any] | None = None,
    max_results: int = 20,
    expand_neighbors: int = 0,
    expand_char_budget: int = DEFAULT_EXPAND_CHAR_BUDGET,
    db: AsyncSession,
    principal: Principal,
    embedder: EmbeddingAdapter,
    vector_store: PgvectorAdapter,
    reranker: Reranker | None = None,
) -> dict[str, Any]:
    """Run a hybrid (semantic + BM25) search across one or more collections.

    The requested ``collection_ids`` are narrowed to those ``principal`` may read;
    searching only unauthorized collections raises ``403``.

    Args:
        query: Natural-language search string.
        collection_ids: Collections to fan out across (at least one).
        top_k: Desired number of results; clamped to ``[1, max_results]``.
        hybrid: When ``True`` fuse BM25 with semantic scores (RRF).
        filters: Optional ``filename``/``tags`` metadata filter.
        max_results: Upper bound on ``top_k`` (from ``mcp.max_results`` config).
        expand_neighbors: A2 adjacency window pulled around each hit (0 = off).
        expand_char_budget: Soft cap on an assembled span's text.
        db: Active async database session.
        principal: Authenticated caller whose read grants scope the search.
        embedder: Embedding adapter for the query vector.
        vector_store: Vector store to search (also does FTS/BM25 scoring).
        reranker: Optional cross-encoder reranker (None when disabled/not loaded).

    Returns:
        A JSON-serialisable ``SearchResponse`` dict with results and stats.
    """
    allowed = await permissions.readable_collection_ids(db, principal, collection_ids)
    if not allowed:
        raise HTTPException(403, permissions.NO_READABLE_COLLECTIONS)
    bounded_top_k = max(_TOP_K_FLOOR, min(top_k, max_results))
    request = SearchRequest.model_validate(
        {
            "query": query,
            "collection_ids": allowed,
            "top_k": bounded_top_k,
            "hybrid": hybrid,
            "filters": filters,
        }
    )
    response = await multi_collection_search(
        request,
        db=db,
        embedder=embedder,
        vector_store=vector_store,
        reranker=reranker,
        expand_neighbors=expand_neighbors,
        expand_char_budget=expand_char_budget,
    )
    result = response.model_dump(mode="json")
    # ranked_shown = the pre-expansion ranked count. more_available (search._more_available) only
    # fires when the candidate pool exceeds len(final), and search_collection caps each collection's
    # contribution at top_k while counting the full pool in returned_after_filter — so whenever it
    # fires, len(final) == top_k == bounded_top_k and matched > bounded_top_k (the "N more" stays
    # positive). A2 coalescing may leave fewer response.results, so count the ranked cut, not those.
    notice = _saturation_notice(response, max_results, bounded_top_k)
    if notice:
        result["notice"] = notice  # only when there is genuinely more below the cut
    return result


async def ingest_document(
    *,
    collection_id: str,
    file_path: str,
    retention_days: int | None = None,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Enqueue a container-local file for ingestion into ``collection_id``.

    **Master key only** — referencing an arbitrary server-side path is an operator
    capability (it can read any file the server can reach), so a scoped user key is
    rejected (enforced in the service). Scoped users upload bytes via ``request_upload``
    (presigned) or the REST API instead. Set ``retention_days`` (1-30) to auto-purge the
    document after that many days; omit for a permanent document.
    """
    return await doc_svc.ingest_local_path(
        db, collection_id, file_path, principal, retention_days=retention_days
    )


async def list_documents(
    *, collection_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """List active documents (with ingestion status) in ``collection_id``.

    Requires a ``read`` grant on the collection. Returns the newest up-to-200
    documents plus ``total`` (the full count); when ``total`` exceeds the returned
    set, narrow the collection or use the REST endpoint's pagination for the rest.
    """
    await permissions.authorize_collection(db, principal, collection_id, "read")
    page = await doc_svc.list_documents(db, collection_id, DocumentListQuery(limit=200))
    return {"documents": page["items"], "total": page["total"]}


async def delete_document(
    *, document_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Soft-delete a document and enqueue async vector + BM25 cleanup.

    Requires a ``write`` grant on the document (or an ancestor collection/workspace).
    """
    # Authorize before resolving so an unpermitted caller can't tell "forbidden"
    # (403) from "nonexistent" (404) — both are 403.
    await permissions.authorize_document(db, principal, document_id, "write")
    collection_id = await doc_svc.resolve_document_collection(db, document_id)
    await doc_svc.delete_document(db, collection_id, document_id)
    return {"document_id": document_id, "collection_id": collection_id, "status": "deleting"}


# ── Document lifecycle (upload / status / download / chunks / reprocess) ───────


def _chunk_view(chunk: SearchResult) -> dict[str, Any]:
    """Compact view of a stored chunk for the inspection tool (no ranking score)."""
    return {"chunk_id": chunk.chunk_id, "text": chunk.text, "metadata": chunk.metadata}


# Bounds for the get_document_chunks inspection tool. A large document can hold thousands of
# chunks, so neither mode returns everything: the by-ids fetch is capped at 100 ids, and the
# paged browse at 100 chunks/page — bounding the JSON-RPC payload either way.
_CHUNK_IDS_MAX = 100
_CHUNK_PAGE_MAX = 100
_CHUNK_PAGE_DEFAULT = 50


async def request_upload(
    *,
    collection_id: str,
    filename: str,
    retention_days: int | None = None,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Reserve a document and return a presigned PUT URL for a direct-to-storage upload.

    Step one of the two-step upload: ``PUT`` the file bytes to the returned ``upload_url``, then
    call ``confirm_upload`` with the ``document_id`` to verify + enqueue ingestion. Requires a
    ``write`` grant on the collection. ``retention_days`` (1-30) auto-purges the document after
    that many days; omit for permanent. Presigned upload needs an S3/MinIO backend.
    """
    return await doc_svc.create_upload(
        db, collection_id, filename, principal, retention_days=retention_days
    )


async def confirm_upload(
    *, document_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Finalize a presigned upload: verify the bytes landed, then enqueue ingestion.

    Step two of the two-step upload — call after the ``PUT`` to ``upload_url`` from
    ``request_upload`` succeeds. Requires a ``write`` grant on the document.
    """
    return await doc_svc.confirm_upload(db, document_id, principal)


async def request_original_upload(
    *, document_id: str, filename: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Reserve a presigned PUT URL to attach an *original source file* to a document.

    **Optional** — keep the raw source (e.g. the PDF a Markdown upload was converted from)
    alongside the parse. The original is stored under the same document and is **never embedded**;
    fetch it later via ``download_document(document_id, original=true)``. Step one of two: ``PUT``
    the bytes to the returned ``upload_url``, then call ``confirm_original_upload``. Requires a
    ``write`` grant on the document; needs an S3/MinIO backend.
    """
    return await doc_svc.create_original_upload(db, document_id, filename, principal)


async def confirm_original_upload(
    *, document_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Finalize an original-source-file attach: verify the bytes landed, then record them.

    Step two of the two-step original attach — call after the ``PUT`` to the ``upload_url`` from
    ``request_original_upload`` succeeds. Requires a ``write`` grant on the document.
    """
    return await doc_svc.confirm_original_upload(db, document_id, principal)


async def reprocess_document(
    *, document_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Re-enqueue a document's ingestion — the manual retry for a failed/stuck file.

    Reuses the stored bytes (nothing re-uploaded) and surfaces a fresh pending job. Requires a
    ``write`` grant on the document.
    """
    await permissions.authorize_document(db, principal, document_id, "write")
    collection_id = await doc_svc.resolve_document_collection(db, document_id)
    return await doc_svc.reprocess_document(db, collection_id, document_id)


async def get_document_status(
    *, document_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Return the latest ingestion job status (or lifecycle state) for a document.

    Requires a ``read`` grant on the document.
    """
    await permissions.authorize_document(db, principal, document_id, "read")
    collection_id = await doc_svc.resolve_document_collection(db, document_id)
    return await doc_svc.get_document_status(db, collection_id, document_id)


async def download_document(
    *, document_id: str, original: bool = False, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Return a short-lived presigned URL to fetch a document's bytes.

    Requires a ``read`` grant on the document. On local-disk storage (which can't presign) the
    response carries a ``notice`` pointing at the REST byte endpoint instead of a ``url``. Set
    ``original=true`` to fetch the attached *original source file* instead of the parse (errors if
    none is attached).
    """
    return await doc_svc.resolve_download_url(db, document_id, principal, original=original)


async def get_document_chunks(
    *,
    document_id: str,
    chunk_ids: list[str] | None = None,
    limit: int = _CHUNK_PAGE_DEFAULT,
    offset: int = 0,
    db: AsyncSession,
    principal: Principal,
    vector_store: PgvectorAdapter,
) -> dict[str, Any]:
    """Inspect a document's stored chunks (text + metadata). Requires a ``read`` grant on it.

    Two modes, so a large document never returns every chunk at once:

    - **By ids** — pass ``chunk_ids`` (e.g. the ``chunk_id``s from ``search_documents`` results) to
      fetch just those chunks, at most 100. Ids belonging to another document are ignored.
    - **Paged** — omit ``chunk_ids`` to page through the document in ``chunk_index`` order via
      ``limit`` (1-100, default 50) + ``offset``; the result carries ``total`` and ``has_more``.
    """
    await permissions.authorize_document(db, principal, document_id, "read")
    collection_id = await doc_svc.resolve_document_collection(db, document_id)

    if chunk_ids:
        wanted = chunk_ids[:_CHUNK_IDS_MAX]
        found = vector_store.chunks_by_ids(collection_id, wanted)
        # Keep only chunks belonging to THIS document. chunks_by_ids matches ids across the whole
        # collection, but the caller may hold only a document-level read grant — a sibling
        # document's chunks must never come back through it.
        chunks = sorted(
            (c for c in found if c.metadata.get("document_id") == document_id),
            key=chunk_index_order_key,
        )
        return {
            "document_id": document_id,
            "collection_id": collection_id,
            "requested": len(wanted),
            "count": len(chunks),
            "chunks": [_chunk_view(c) for c in chunks],
        }

    page_limit = max(1, min(limit, _CHUNK_PAGE_MAX))
    page_offset = max(0, offset)
    chunks, total = vector_store.document_chunks(
        collection_id, document_id, limit=page_limit, offset=page_offset
    )
    return {
        "document_id": document_id,
        "collection_id": collection_id,
        "total": total,
        "limit": page_limit,
        "offset": page_offset,
        "count": len(chunks),
        "has_more": page_offset + len(chunks) < total,
        "chunks": [_chunk_view(c) for c in chunks],
    }


# ── Workspace & collection CRUD ───────────────────────────────────────────────


async def create_workspace(
    *,
    name: str,
    description: str = "",
    color: str = "#6366f1",
    icon: str = "folder",
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Create a top-level workspace (requires the ``create_workspace`` capability).

    A scoped (non-master) creator is auto-granted write on the new workspace so they can use it.
    """
    await permissions.authorize_workspace_creation(db, principal)
    workspace = await ws_svc.create_workspace(
        name=name, description=description, color=color, icon=icon, db=db
    )
    await permissions.grant_creator_access(db, principal, workspace["id"])
    return workspace


async def update_workspace(
    *,
    workspace_id: str,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    icon: str | None = None,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Edit a workspace's metadata (only the fields you pass). Requires ``write`` on it."""
    await permissions.authorize_workspace(db, principal, workspace_id, "write")
    body = WorkspaceUpdate(name=name, description=description, color=color, icon=icon)
    return await ws_svc.update_workspace(workspace_id, body, db)


async def delete_workspace(
    *, workspace_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Delete a workspace, cascading to its collections + documents. Requires ``write`` on it."""
    await permissions.authorize_workspace(db, principal, workspace_id, "write")
    await ws_svc.delete_workspace(workspace_id, db)
    return {"workspace_id": workspace_id, "status": "deleted"}


async def create_collection(
    *,
    workspace_id: str,
    name: str,
    description: str = "",
    color: str = "#8b5cf6",
    icon: str = "book",
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Create a collection inside a workspace. Requires ``write`` on the workspace."""
    await permissions.authorize_workspace(db, principal, workspace_id, "write")
    return await collection_svc.create_collection(
        workspace_id=workspace_id, name=name, description=description,
        color=color, icon=icon, db=db,
    )


async def update_collection(
    *,
    workspace_id: str,
    collection_id: str,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    icon: str | None = None,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Edit a collection's metadata (only the fields you pass). Requires ``write`` on it or an ancestor."""
    await permissions.authorize_collection(db, principal, collection_id, "write")
    body = CollectionUpdate(name=name, description=description, color=color, icon=icon)
    return await collection_svc.update_collection(workspace_id, collection_id, body, db)


async def delete_collection(
    *, workspace_id: str, collection_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Delete a collection, cascading to its documents. Requires ``write`` on it or an ancestor."""
    await permissions.authorize_collection(db, principal, collection_id, "write")
    await collection_svc.delete_collection(workspace_id, collection_id, db)
    return {"workspace_id": workspace_id, "collection_id": collection_id, "status": "deleted"}


# ── Tags ──────────────────────────────────────────────────────────────────────


async def list_tags(
    *, workspace_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """List a workspace's tags with usage counts. Requires the ``manage_tags`` capability."""
    await permissions.authorize_tag_management(db, principal, workspace_id)
    return {"tags": await tag_svc.list_tags(workspace_id, db)}


async def create_tag(
    *, workspace_id: str, name: str, color: str | None = None,
    db: AsyncSession, principal: Principal,
) -> dict[str, Any]:
    """Create a tag in a workspace. Requires the ``manage_tags`` capability."""
    await permissions.authorize_tag_management(db, principal, workspace_id)
    return await tag_svc.create_tag(workspace_id, name, color, db)


async def update_tag(
    *, workspace_id: str, tag_id: str, name: str | None = None, color: str | None = None,
    db: AsyncSession, principal: Principal,
) -> dict[str, Any]:
    """Rename/recolor a tag (only the fields you pass). Requires the ``manage_tags`` capability."""
    await permissions.authorize_tag_management(db, principal, workspace_id)
    return await tag_svc.update_tag(
        workspace_id, tag_id, TagUpdate(name=name, color=color), db
    )


async def delete_tag(
    *, workspace_id: str, tag_id: str, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Delete a tag (removing its assignments). Requires the ``manage_tags`` capability."""
    await permissions.authorize_tag_management(db, principal, workspace_id)
    await tag_svc.delete_tag(workspace_id, tag_id, db)
    return {"workspace_id": workspace_id, "tag_id": tag_id, "status": "deleted"}


async def merge_tags(
    *, workspace_id: str, source_id: str, target_id: str,
    db: AsyncSession, principal: Principal,
) -> dict[str, Any]:
    """Merge ``source_id``'s assignments into ``target_id``, then delete the source tag.

    Requires the ``manage_tags`` capability.
    """
    await permissions.authorize_tag_management(db, principal, workspace_id)
    return await tag_svc.merge_tags(
        workspace_id, TagMerge(source_id=source_id, target_id=target_id), db
    )


async def _authorize_and_apply_tag(
    db: AsyncSession,
    principal: Principal,
    *,
    workspace_id: str,
    tag_id: str,
    target: str,
    collection_id: str | None,
    document_id: str | None,
    assign: bool,
) -> None:
    """Authorize + (un)assign a tag on a workspace/collection/document target.

    Mirrors the REST tag routes' two-tier authz: the ``manage_tags`` capability is the privilege
    gate; a collection/document target additionally needs ``write`` on that resource, so a scoped
    tag-manager can't tag resources their data grants hide.
    """
    await permissions.authorize_tag_management(db, principal, workspace_id)
    if target == "workspace":
        # Reject a resource id on a workspace target so a caller who forgot ``target="collection"``
        # doesn't silently tag the whole workspace instead of the resource they named.
        if collection_id or document_id:
            raise HTTPException(
                422, "collection_id/document_id are only valid with target 'collection'/'document'"
            )
        if assign:
            await tag_svc.assign_workspace_tag(workspace_id, tag_id, db)
        else:
            await tag_svc.unassign_workspace_tag(workspace_id, tag_id, db)
    elif target == "collection":
        if not collection_id:
            raise HTTPException(422, "collection_id is required for a collection tag target")
        await permissions.authorize_collection(db, principal, collection_id, "write")
        if assign:
            await tag_svc.assign_collection_tag(workspace_id, collection_id, tag_id, db)
        else:
            await tag_svc.unassign_collection_tag(workspace_id, collection_id, tag_id, db)
    elif target == "document":
        if not collection_id or not document_id:
            raise HTTPException(
                422, "collection_id and document_id are required for a document tag target"
            )
        await permissions.authorize_document(db, principal, document_id, "write")
        if assign:
            await tag_svc.assign_document_tag(workspace_id, collection_id, document_id, tag_id, db)
        else:
            await tag_svc.unassign_document_tag(workspace_id, collection_id, document_id, tag_id, db)
    else:
        raise HTTPException(
            422, f"Unknown tag target {target!r} (expected workspace|collection|document)"
        )


async def assign_tag(
    *,
    workspace_id: str,
    tag_id: str,
    target: str = "workspace",
    collection_id: str | None = None,
    document_id: str | None = None,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Attach a tag to a ``workspace``, ``collection``, or ``document`` (set ``target``).

    ``collection_id`` is required for a collection/document target; ``document_id`` for a document.
    Requires the ``manage_tags`` capability (and ``write`` on a collection/document target).
    """
    await _authorize_and_apply_tag(
        db, principal, workspace_id=workspace_id, tag_id=tag_id, target=target,
        collection_id=collection_id, document_id=document_id, assign=True,
    )
    return {"workspace_id": workspace_id, "tag_id": tag_id, "target": target, "status": "assigned"}


async def unassign_tag(
    *,
    workspace_id: str,
    tag_id: str,
    target: str = "workspace",
    collection_id: str | None = None,
    document_id: str | None = None,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Detach a tag from a ``workspace``, ``collection``, or ``document`` (set ``target``).

    Same arguments + authorization as :func:`assign_tag`.
    """
    await _authorize_and_apply_tag(
        db, principal, workspace_id=workspace_id, tag_id=tag_id, target=target,
        collection_id=collection_id, document_id=document_id, assign=False,
    )
    return {
        "workspace_id": workspace_id, "tag_id": tag_id, "target": target, "status": "unassigned",
    }


# ── Ingestion queue + rate limits ─────────────────────────────────────────────


async def list_ingestion_jobs(
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    collection: str | None = None,
    filename: str | None = None,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """List ingestion jobs (active first, then newest-first), scoped to the caller's readable collections.

    Filters (all optional, AND-combined): ``status`` (exact), ``collection`` (name substring),
    ``filename`` (substring). Returns ``{items, total, limit, offset}``.
    """
    scope = await permissions.readable_collection_scope(db, principal)
    # Clamp to JobListQuery's accepted band (1..200 / >=0) so an LLM passing an out-of-range page
    # size gets a sensible page instead of a validation error (cf. search_documents clamping top_k).
    query = JobListQuery(
        limit=max(1, min(limit, 200)), offset=max(0, offset),
        status=status, collection=collection, filename=filename,
    )
    return await jobs_svc.list_jobs(db, query, scope)


async def get_ingestion_stats(
    *, embedding_pause_seconds: int, db: AsyncSession, principal: Principal
) -> dict[str, Any]:
    """Live ingestion-queue totals plus the global embedding-quota backoff.

    ``counts`` is the per-status job count scoped to the caller's readable collections;
    ``embedding_paused_seconds`` is the remaining provider-rate-limit pause (0 = running).
    """
    scope = await permissions.readable_collection_scope(db, principal)
    return {
        "counts": await jobs_svc.job_status_counts(db, scope),
        "embedding_paused_seconds": embedding_pause_seconds,
    }


async def get_rate_limit(
    *, rate_limit: dict[str, Any], embedding_pause_seconds: int
) -> dict[str, Any]:
    """Report the caller's current MCP call budget + any global ingestion pause.

    ``mcp`` is the caller's per-key token bucket (``{limit_rpm, remaining, reset_seconds}``);
    ``reset_seconds`` is how long until at least one more MCP call is allowed (0 = calls available).
    ``ingestion.embedding_paused_seconds`` is the remaining provider-quota backoff pausing ingestion.
    """
    return {
        "mcp": rate_limit,
        "ingestion": {"embedding_paused_seconds": embedding_pause_seconds},
    }
