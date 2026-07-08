"""Search service: BM25 helpers, single-collection search, multi-collection fan-out."""

import asyncio
from time import monotonic

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, Reranker
from api.adapters.vector_store.pgvector import PgvectorAdapter
from api.constants import DEFAULT_EXPAND_CHAR_BUDGET
from api.models.search import (
    CollectionStat,
    SearchFilters,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SourceProvenance,
)
from api.services.expansion import expand_spans

logger = structlog.get_logger()

_DEFAULT_FAN_OUT = 4


def _matches(result: SearchResult, filters: SearchFilters) -> bool:
    """Determine whether a search result satisfies the given filters.

    Args:
        result: The search result to evaluate.
        filters: The filters to apply to the search result.

    Returns:
        True if the result matches all provided filters, otherwise False.
    """
    language = filters.language
    filename = filters.filename
    tags = filters.tags

    if not language and not filename and not tags:
        return True
    if language and result.metadata.get("language") != language:
        return False
    if filename and result.metadata.get("filename") != filename:
        return False
    if tags:
        result_tags = set(result.metadata.get("tags", []))
        if not set(tags).issubset(result_tags):
            return False
    return True


def apply_filters(results: list[SearchResult], filters: SearchFilters | None) -> list[SearchResult]:
    """Filter search results based on the provided criteria.

    Args:
        results: A list of search results to filter.
        filters: Optional filters to apply. If None, the original results are returned.

    Returns:
        A list of search results that match the provided filters.
    """
    if not filters:
        return results
    return [result for result in results if _matches(result, filters)]


def _rank_by_bm25(results: list[SearchResult], scores: dict[str, float]) -> list[SearchResult]:
    """Return results sorted by BM25 score (keyed by chunk_id).

    Args:
        results: Candidate search results from the vector store.
        scores: BM25 scores keyed by chunk_id.

    Returns:
        Results sorted descending by the chunk's BM25 score.
    """
    return sorted(
        results,
        key=lambda r: scores.get(r.chunk_id, 0.0),
        reverse=True,
    )


def _rank_candidates(
    candidates: list[SearchResult],
    query: str,
    mode: SearchMode,
    collection_id: str,
    vector_store: PgvectorAdapter,
) -> tuple[list[SearchResult], SearchMode]:
    """Re-rank vector candidates for the non-fused modes; fall back on no match.

    SEMANTIC keeps the vector order. BM25 re-ranks the candidates purely by BM25
    score, degrading to SEMANTIC_ONLY (vector order) when no candidate matches the
    query's keywords. HYBRID does NOT flow through here — it is fused in one SQL
    round-trip by ``PgvectorAdapter.hybrid_search`` (Phase 4 item 2).

    Args:
        candidates: Vector-store hits to re-rank.
        query: Raw query text for FTS parsing.
        mode: Requested ranking mode (SEMANTIC or BM25).
        collection_id: Collection whose chunks to score.
        vector_store: Adapter providing Postgres FTS (``bm25_scores``).

    Returns:
        Tuple of (ranked results, effective mode).
    """
    if mode == SearchMode.SEMANTIC:
        return candidates, SearchMode.SEMANTIC
    bm25_scores = vector_store.bm25_scores(
        collection_id, query, [c.chunk_id for c in candidates]
    )
    if not bm25_scores:
        return candidates, SearchMode.SEMANTIC_ONLY
    # ponytail: BM25-only re-ranks the vector candidate set. For unbounded keyword
    # recall, query FTS without the id filter (HYBRID already does full-collection).
    ranked = _rank_by_bm25(candidates, bm25_scores)
    for rank, result in enumerate(ranked, start=1):
        result.rank = rank
        result.score = bm25_scores.get(result.chunk_id, 0.0)  # report the real BM25 score
    return ranked, SearchMode.BM25


def search_collection(
    collection_id: str,
    query_vector: list[float],
    query: str,
    top_k: int,
    *,
    fan_out: int = _DEFAULT_FAN_OUT,
    mode: SearchMode = SearchMode.HYBRID,
    alpha: float = 0.7,
    filters: SearchFilters | None = None,
    vector_store: PgvectorAdapter,
    reranker: Reranker | None = None,
) -> tuple[list[SearchResult], SearchMode, int, int]:
    """Search a single collection and return ranked results.

    Args:
        collection_id: The collection to search.
        query_vector: Pre-computed embedding of the query.
        query: Raw query text (used for FTS/BM25).
        top_k: Maximum number of results to return after filtering.
        fan_out: Multiplier for pre-filter candidate retrieval (clamped to 1–10).
        mode: Ranking mode (HYBRID, SEMANTIC, or BM25).
        alpha: Semantic weight in the HYBRID RRF fusion (BM25 gets 1-alpha).
        filters: Optional metadata filters applied after ranking.
        vector_store: Adapter for vector similarity search and FTS scoring.
        reranker: Optional cross-encoder; when set, reorders the over-fetched
            candidate pool by query-document relevance before the top_k cut.

    Returns:
        Tuple of (results, search_mode, retrieved_before_filter, returned_after_filter).
    """
    pool_size = top_k * min(max(fan_out, 1), 10)
    if mode == SearchMode.HYBRID:
        # Phase 4 item 2: vector + BM25 fused in one SQL round-trip. Empty BM25 →
        # SEMANTIC_ONLY (hybrid_search already returns real cosine scores there).
        fused, bm25_matched = vector_store.hybrid_search(
            collection_id, query_vector, query, pool_size, alpha=alpha, filters=filters
        )
        results = fused
        effective_mode = SearchMode.HYBRID if bm25_matched else SearchMode.SEMANTIC_ONLY
    else:
        candidates = vector_store.search(
            collection_id, query_vector, pool_size, filters=filters
        )
        results, effective_mode = _rank_candidates(
            candidates, query, mode, collection_id, vector_store
        )
    retrieved = len(results)
    # Postgres already pre-filtered in SQL (Phase 4); apply_filters stays as a
    # backend-agnostic guard (the only filter on the in-memory test fakes) and is a
    # cheap no-op once the WHERE has done the work.
    filtered = apply_filters(results, filters)
    if reranker is not None:
        try:
            filtered = reranker.rerank(query, filtered)
        except Exception as exc:  # backstop: an optional stage must never 500 the search
            logger.warning("reranker failed; using pre-rerank order", error=str(exc))
    return filtered[:top_k], effective_mode, retrieved, len(filtered)


def _apply_provenance(
    results: list[SearchResult], col_id: str, info: dict[str, str]
) -> None:
    """Attach SourceProvenance to each result in-place.

    Args:
        results: Results to annotate.
        col_id: Collection UUID.
        info: Mapping with keys collection_name, workspace_id, workspace_name.
    """
    for r in results:
        r.source = SourceProvenance(
            collection_id=col_id,
            collection_name=info["collection_name"],
            workspace_id=info["workspace_id"],
            workspace_name=info["workspace_name"],
            document_id=r.metadata.get("document_id"),
            filename=r.metadata.get("filename"),
            page_number=r.metadata.get("page_number"),
        )


_RRF_K = 60


def _merge_collections_rrf(per_collection: list[list[SearchResult]]) -> list[SearchResult]:
    """Fuse per-collection result lists with second-level Reciprocal Rank Fusion.

    RRF re-scores every result by ``1 / (k + rank_within_collection)`` to normalise
    the differing raw-score scales ACROSS backends (e.g. Chroma/pgvector
    ``1 - distance`` vs Qdrant's native similarity) so no single collection's score
    range dominates the merge.

    But that fusion score is a rank-only value (identical for every query) — so when
    only ONE collection contributed results there is nothing to normalise, and
    overwriting would replace the real relevance score (similarity / BM25 / hybrid)
    with a meaningless constant. In that case we keep the per-collection scores as-is;
    RRF only kicks in for a genuine cross-collection merge. Results are copied via
    model_copy() so the per-collection originals are not mutated.

    Args:
        per_collection: One rank-ordered result list per collection.

    Returns:
        New, globally re-ranked list of copied SearchResult objects.
    """
    contributing = [results for results in per_collection if results]
    if len(contributing) == 1:
        # Single source: preserve the real scores; the list is already rank-ordered.
        merged = [result.model_copy() for result in contributing[0]]
        for rank, result in enumerate(merged, start=1):
            result.rank = rank
        return merged

    fused: list[SearchResult] = []
    for results in per_collection:
        for rank, result in enumerate(results, start=1):
            copy = result.model_copy()
            copy.score = 1.0 / (_RRF_K + rank)
            fused.append(copy)
    ordered = sorted(fused, key=lambda r: r.score, reverse=True)
    for rank, result in enumerate(ordered, start=1):
        result.rank = rank
    return ordered


def _update_top_k_stats(final: list[SearchResult], stats: dict[str, CollectionStat]) -> None:
    """Increment contributed_to_top_k for each collection that appears in final.

    Args:
        final: Truncated top-k result list.
        stats: Per-collection stats dict to update in-place.
    """
    for r in final:
        source = r.source
        if source is not None and source.collection_id in stats:
            stats[source.collection_id].contributed_to_top_k += 1


async def _get_collections_info(
    db: AsyncSession, col_ids: list[str]
) -> dict[str, dict[str, str]]:
    """Batch-fetch collection + workspace metadata for the given collection ids.

    A single query (rather than one per collection) keeps all DB access on the
    event loop, so the per-collection searches can safely fan out to threads
    without sharing the AsyncSession across them.

    Args:
        db: Active async database session.
        col_ids: Collection ids to look up.

    Returns:
        Mapping of collection id → {collection_name, workspace_id, workspace_name};
        unknown ids are simply absent from the mapping.
    """
    from api.db import collections as col_t
    from api.db import workspaces as ws_t

    rows = (
        await db.execute(
            select(
                col_t.c.id, col_t.c.name, col_t.c.workspace_id,
                ws_t.c.name.label("workspace_name"),
            )
            .join(ws_t, col_t.c.workspace_id == ws_t.c.id)
            .where(col_t.c.id.in_(col_ids))
        )
    ).fetchall()
    return {
        str(row.id): {
            "collection_name": str(row.name),
            "workspace_id": str(row.workspace_id),
            "workspace_name": str(row.workspace_name),
        }
        for row in rows
    }


def _fan_out_one(
    col_id: str,
    query_vector: list[float],
    request: SearchRequest,
    *,
    vector_store: PgvectorAdapter,
    fan_out: int,
    reranker: Reranker | None = None,
) -> tuple[list[SearchResult], SearchMode, int, int]:
    """Thread target: run one collection's search (no DB access on this thread).

    Args:
        col_id: Collection to search.
        query_vector: Pre-computed query embedding.
        request: Full search request (top_k, hybrid, filters, etc.).
        vector_store: Vector similarity search + FTS adapter.
        fan_out: Candidate multiplier applied before filtering.
        reranker: Optional cross-encoder reranker (skipped when None).

    Returns:
        (results, mode, retrieved_before_filter, returned_after_filter).
    """
    return search_collection(
        col_id, query_vector, request.query, request.top_k,
        fan_out=fan_out, mode=request.resolved_mode(), alpha=request.hybrid_alpha,
        filters=request.filters, vector_store=vector_store,
        reranker=reranker,
    )


def _collect_results(
    known: list[str],
    outcomes: list[tuple[list[SearchResult], SearchMode, int, int]],
    infos: dict[str, dict[str, str]],
) -> tuple[list[list[SearchResult]], dict[str, CollectionStat], SearchMode | None]:
    """Annotate provenance, build per-collection stats, and detect fallback mode.

    Args:
        known: Collection ids that resolved, aligned with ``outcomes``.
        outcomes: Per-collection ``_fan_out_one`` return tuples.
        infos: Collection metadata keyed by collection id.

    Returns:
        (per_collection_results, stats, fallback) where ``fallback`` is
        SEMANTIC_ONLY if any collection fell back, otherwise None.
    """
    stats: dict[str, CollectionStat] = {}
    per_collection: list[list[SearchResult]] = []
    fallback: SearchMode | None = None
    for cid, (results, col_mode, retrieved, returned) in zip(known, outcomes, strict=True):
        info = infos[cid]
        _apply_provenance(results, cid, info)
        stats[cid] = CollectionStat(
            name=info["collection_name"], workspace_name=info["workspace_name"],
            retrieved_before_filter=retrieved, returned_after_filter=returned,
        )
        if col_mode == SearchMode.SEMANTIC_ONLY:
            fallback = SearchMode.SEMANTIC_ONLY
        per_collection.append(results)
    return per_collection, stats, fallback


def _more_available(final: list[SearchResult], stats: dict[str, CollectionStat]) -> bool:
    """True when the ranked candidate pool held more chunks than were returned **and** the
    returned results sit on a score *plateau* — the last shown chunk is nearly as relevant as
    the first — so the ``top_k`` cut likely severed comparably-relevant matches (plan A3: the
    MCP warns "there's more"). A sharp score *elbow* means the results are genuinely complete,
    so we stay quiet and the signal keeps its meaning. The 0.9 plateau ratio is an initial
    default to tune against an eval set; negative reranker logits (``top <= 0``) stay quiet.
    """
    pool = sum(s.returned_after_filter for s in stats.values())
    if pool <= len(final) or len(final) < 2:
        return False
    top, tail = final[0].score, final[-1].score
    return top > 0 and tail >= 0.9 * top


async def _expand(
    final: list[SearchResult],
    vector_store: PgvectorAdapter,
    neighbors: int,
    char_budget: int,
) -> list[SearchResult]:
    """A2 adjacency expansion as a post-pass, off the event loop.

    Grows each hit into its adjacency span (see ``api.services.expansion``). Any failure degrades
    to the un-expanded hits — an optional completeness stage must never turn a search into a 500,
    matching the reranker's contract.
    """
    if neighbors <= 0 or not final:
        return final
    try:
        return await asyncio.to_thread(
            expand_spans,
            final,
            neighbors=neighbors,
            char_budget=char_budget,
            fetch=vector_store.chunks_by_ids,
        )
    except Exception as exc:  # backstop: expansion must never 500 the search
        logger.warning("span expansion failed; returning un-expanded hits", error=str(exc))
        return final


async def multi_collection_search(
    request: SearchRequest,
    *,
    db: AsyncSession,
    embedder: EmbeddingAdapter,
    vector_store: PgvectorAdapter,
    reranker: Reranker | None = None,
    expand_neighbors: int = 0,
    expand_char_budget: int = DEFAULT_EXPAND_CHAR_BUDGET,
) -> SearchResponse:
    """Search across one or more collections and merge with second-level RRF.

    Embeds the query once, batch-loads collection metadata, fans out to each
    collection concurrently via ``asyncio.gather`` (each search runs in a worker
    thread so the blocking vector-store/BM25 calls do not stall the event loop),
    then fuses the per-collection results with Reciprocal Rank Fusion.

    Args:
        request: Parsed SearchRequest from the caller.
        db: Async database session for metadata look-ups.
        embedder: Embedding adapter used to vectorise the query.
        vector_store: Vector store adapter for similarity search + FTS scoring.
        reranker: Optional cross-encoder reranker applied per collection before
            the cross-collection merge; ``None`` skips the stage (RRF-only).
        expand_neighbors: A2 adjacency window — chunks pulled on each side of every hit and
            coalesced into spans after the top_k cut (0 = off). Degrades to un-expanded hits.
        expand_char_budget: Soft cap on an assembled span's text.

    Returns:
        SearchResponse with ranked results, stats, and timing fields.
    """
    t0 = monotonic()
    # Off the event loop: the Ollama adapter's embed() calls asyncio.run() (which
    # raises if invoked on the running loop), and the sentence-transformers one is
    # CPU-blocking — a worker thread is correct for both.
    query_vector = await asyncio.to_thread(embedder.embed, request.query)
    embed_ms = int((monotonic() - t0) * 1000)
    fan_out = request.fan_out if request.fan_out is not None else _DEFAULT_FAN_OUT
    infos = await _get_collections_info(db, request.collection_ids)
    known = [cid for cid in request.collection_ids if cid in infos]
    outcomes = await asyncio.gather(*[
        asyncio.to_thread(
            _fan_out_one, cid, query_vector, request,
            vector_store=vector_store, fan_out=fan_out,
            reranker=reranker,
        )
        for cid in known
    ])
    per_collection, stats, fallback = _collect_results(known, list(outcomes), infos)
    mode = fallback or request.resolved_mode()
    final = _merge_collections_rrf(per_collection)[: request.top_k]
    _update_top_k_stats(final, stats)
    # Saturation + delivery are measured on the ranked top_k hits BEFORE expansion: A2 completes
    # each hit with adjacent context, it does not change what ranked or how many hits we got.
    under_delivered = len(final) < request.top_k
    more_available = _more_available(final, stats)
    results = await _expand(final, vector_store, expand_neighbors, expand_char_budget)
    total_ms = int((monotonic() - t0) * 1000)
    return SearchResponse(
        results=results, collection_stats=stats, query_embedding_ms=embed_ms,
        search_ms=total_ms - embed_ms, total_ms=total_ms,
        search_mode=mode, under_delivered=under_delivered,
        more_available=more_available,
    )
