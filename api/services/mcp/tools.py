"""MCP tool implementations.

Each function is a thin, framework-agnostic wrapper over an existing service, so
the same logic is exercised by both the ``FastMCP`` server
(:mod:`api.services.mcp.server`) and the integration tests — no SSE transport is
needed to test a tool. The server layer is responsible for resolving the
``db``/``embedder``/``vector_store``/``redis_client`` dependencies and passing
them in as keyword arguments.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, Reranker
from api.adapters.vector_store.pgvector import PgvectorAdapter
from api.constants import DEFAULT_EXPAND_CHAR_BUDGET, MAX_TOP_K
from api.models.search import SearchRequest, SearchResponse
from api.services import documents as doc_svc
from api.services import workspaces as ws_svc
from api.services.auth import Principal
from api.services.search import multi_collection_search

# MCP authenticates with the master key (see api.services.mcp.middleware), so the
# tools run with full access. The principal is still threaded through so the
# collection-scoping checks stay honest and unit-testable.
MASTER_PRINCIPAL = Principal(is_master=True)

_TOP_K_FLOOR = 1

# Cap on how many documents the notice names inline; the full list is always in the structured
# ``coverage`` field. Keeps the LLM-facing text bounded when a query matches many documents.
_NOTICE_DOC_LIMIT = 5


def _saturation_notice(
    response: SearchResponse, max_results: int, ranked_shown: int
) -> str | None:
    """A natural-language "there's more" hint for the model when relevant chunks fell below the
    ``top_k`` cut (plan A3 — the MCP must tell the caller it returned only part of what matched).

    Returns ``None`` when results look complete (``more_available`` is false), so the warning
    stays meaningful and the model doesn't learn to ignore it. The LLM reads this text, not the
    structured ``more_available``/``coverage`` fields, so it is phrased as an actionable instruction
    and it *names the documents* with hidden matches (from ``coverage``) so the caller knows where
    to look — and, once the A4 fetch primitives land, which document to expand.

    ``ranked_shown`` is the number of ranked hits shown *before* A2 expansion coalesces them into
    spans. The notice describes the **ranking** cut (what a higher ``top_k`` changes), so its counts
    must stay on the pre-expansion scale that ``more_available`` was measured on — not the
    possibly-fewer merged spans in ``response.results``.
    """
    if not response.more_available:
        return None
    matched = sum(s.returned_after_filter for s in response.collection_stats.values())
    lines = [
        f"{matched} chunks matched and were ranked; showing the top {ranked_shown}. About "
        f"{matched - ranked_shown} more, of comparable relevance, fell below the top_k cut."
    ]
    # Name the documents whose matches were cut, most-hidden first (coverage is pre-sorted).
    for c in response.coverage[:_NOTICE_DOC_LIMIT]:
        who = c.filename or c.document_id or "an unnamed document"
        lines.append(
            f"- {who}: showing {c.returned} of {c.matched} matched "
            f"({c.matched - c.returned} more below the cut)"
        )
    hidden = len(response.coverage) - _NOTICE_DOC_LIMIT
    if hidden > 0:
        lines.append(f"- and {hidden} more document(s) with matches below the cut")
    # Only suggest raising top_k when there's headroom; at the ceiling it's a dead-end (the request
    # is already clamped to max_results), so the notice just reports what's hidden without it.
    if ranked_shown < max_results:
        lines.append(
            f"If the answer seems incomplete, re-run search_documents with a higher top_k "
            f"(up to {max_results})."
        )
    return "\n".join(lines)


async def list_workspaces(*, db: AsyncSession) -> dict[str, Any]:
    """Return the workspace tree with per-collection and per-workspace counts."""
    return {"workspaces": await ws_svc.list_workspace_tree(db)}


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
    embedder: EmbeddingAdapter,
    vector_store: PgvectorAdapter,
    reranker: Reranker | None = None,
) -> dict[str, Any]:
    """Run a hybrid (semantic + BM25) search across one or more collections.

    Args:
        query: Natural-language search string.
        collection_ids: Collections to fan out across (at least one).
        top_k: Desired number of results; clamped to ``[1, max_results]`` and never above the
            ``MAX_TOP_K`` model ceiling (so a ``max_results`` mis-set above it can't 500).
        hybrid: When ``True`` fuse BM25 with semantic scores (RRF).
        filters: Optional ``filename``/``tags`` metadata filter.
        max_results: Upper bound on ``top_k`` (from ``mcp.max_results`` config).
        expand_neighbors: A2 adjacency window pulled around each hit (0 = off).
        expand_char_budget: Soft cap on an assembled span's text.
        db: Active async database session.
        embedder: Embedding adapter for the query vector.
        vector_store: Vector store to search (also does FTS/BM25 scoring).
        reranker: Optional cross-encoder reranker (None when disabled/not loaded).

    Returns:
        A JSON-serialisable ``SearchResponse`` dict with results and stats.
    """
    # mcp.max_results has no hard upper bound, but SearchRequest.top_k does (MAX_TOP_K). Cap the
    # effective ceiling at both and use it everywhere below: bounded_top_k stays within the model
    # bound (no 500 from model_validate), and the saturation notice won't advertise headroom past
    # MAX_TOP_K that a re-run could never deliver.
    effective_max_results = min(max_results, MAX_TOP_K)
    bounded_top_k = max(_TOP_K_FLOOR, min(top_k, effective_max_results))
    request = SearchRequest.model_validate(
        {
            "query": query,
            "collection_ids": collection_ids,
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
    # fires when the candidate pool exceeds len(final); multi_collection_search applies the single
    # [:top_k] cut after fusing collections, while returned_after_filter counts the full pool — so
    # whenever it fires, len(final) == top_k == bounded_top_k and matched > bounded_top_k (the "N
    # more" stays positive). A2 coalescing may leave fewer response.results, so count the ranked cut.
    notice = _saturation_notice(response, effective_max_results, bounded_top_k)
    if notice:
        result["notice"] = notice  # only when there is genuinely more below the cut
    return result


async def ingest_document(
    *,
    collection_id: str,
    file_path: str,
    temporary: bool = False,
    db: AsyncSession,
    principal: Principal = MASTER_PRINCIPAL,
) -> dict[str, Any]:
    """Enqueue a container-local file for ingestion into ``collection_id``.

    Set ``temporary`` to auto-purge the document after ``storage.temp_retention_hours``
    (a no-op when retention is 0).
    """
    return await doc_svc.ingest_local_path(
        db, collection_id, file_path, principal, temporary=temporary
    )


async def list_documents(*, collection_id: str, db: AsyncSession) -> dict[str, Any]:
    """List active documents (with ingestion status) in ``collection_id``."""
    return {"documents": await doc_svc.list_documents(db, collection_id)}


async def delete_document(
    *,
    document_id: str,
    db: AsyncSession,
    principal: Principal = MASTER_PRINCIPAL,
) -> dict[str, Any]:
    """Soft-delete a document and enqueue async vector + BM25 cleanup."""
    collection_id = await doc_svc.resolve_document_collection(db, document_id)
    if not principal.can_access(collection_id):
        raise HTTPException(403, "API key not valid for this collection")
    await doc_svc.delete_document(db, collection_id, document_id)
    return {"document_id": document_id, "collection_id": collection_id, "status": "deleting"}
