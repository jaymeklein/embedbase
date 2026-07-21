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
from api.adapters.vector_store.pgvector import PgvectorAdapter
from api.constants import DEFAULT_EXPAND_CHAR_BUDGET
from api.models.document import DocumentListQuery
from api.models.search import SearchRequest, SearchResponse
from api.services import documents as doc_svc
from api.services import permissions
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
    temporary: bool = False,
    db: AsyncSession,
    principal: Principal,
) -> dict[str, Any]:
    """Enqueue a container-local file for ingestion into ``collection_id``.

    **Master key only** — referencing an arbitrary server-side path is an operator
    capability (it can read any file the server can reach), so a scoped user key is
    rejected (enforced in the service). Scoped users upload bytes via the REST API
    instead. Set ``temporary`` to auto-purge the document after
    ``storage.temp_retention_hours`` (a no-op when retention is 0).
    """
    return await doc_svc.ingest_local_path(
        db, collection_id, file_path, principal, temporary=temporary
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
