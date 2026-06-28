"""Search service: BM25 helpers, single-collection search, multi-collection fan-out."""

import asyncio
from time import monotonic
from typing import Any

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, Reranker, VectorStoreAdapter
from api.models.redis import CorpusConfig
from api.models.search import (
    CollectionStat,
    SearchFilters,
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SourceProvenance,
)
from api.services.bm25 import score_semantic, score_structured
from api.services.logs import debug
from api.services.redis.redis import get_corpus, get_corpus_version

_DEFAULT_FAN_OUT = 4

# Version-keyed in-process cache: collection_id → (version, index, chunk_ids)
_bm25_cache: dict[str, tuple[int, BM25Okapi, list[str]]] = {}


def _get_cached(collection_id: str) -> tuple[int, BM25Okapi, list[str]] | None:
    return _bm25_cache.get(collection_id)


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


def _get_bm25_scores(
    redis_client: Any,
    corpus_config: CorpusConfig,
    query: str,
) -> dict[str, float]:
    """Compute BM25 scores for each corpus entry against the given query.

    Maintains a version-keyed in-process cache so the BM25Okapi index is only
    rebuilt when a document is added or removed from the collection.

    Args:
        redis_client: An active Redis client instance.
        corpus_config: Configuration holding the Redis keys for the corpus and version.
        query: The search query string to score against.

    Returns:
        A mapping of chunk_id to BM25 score for every entry in the corpus.
        Returns an empty dict when the corpus is empty or unavailable.
    """
    version = get_corpus_version(redis_client, corpus_config)
    collection_id = corpus_config.collection_id
    cached = _get_cached(collection_id)
    if cached is None or cached[0] != version:
        corpus = get_corpus(redis_client, corpus_config)
        if not corpus.data:
            return {}
        chunk_ids = corpus.chunk_ids
        tokenized = corpus.tokenized
        index = BM25Okapi(tokenized)
        _bm25_cache[collection_id] = (version, index, chunk_ids)
        debug(
            "rebuilt BM25 index for collection %s at version %d (%d entries)",
            collection_id,
            version,
            len(chunk_ids),
        )
    else:
        _, index, chunk_ids = cached
    scores: list[float] = index.get_scores(query.lower().split()).tolist()
    return dict(zip(chunk_ids, scores, strict=True))


def _reciprocal_rank_fusion(
    vector_results: list[SearchResult],
    bm25_results: list[SearchResult],
    alpha: float = 0.7,
    k: int = 60,
) -> list[SearchResult]:
    """Merge two ranked lists using Reciprocal Rank Fusion.

    Args:
        vector_results: Results ranked by semantic similarity.
        bm25_results: Results ranked by BM25 keyword score.
        alpha: Weight for the semantic ranking (1-alpha goes to BM25).
        k: RRF damping constant (default 60).

    Returns:
        A merged, re-ranked list of SearchResult objects.
    """
    scored_semantic = score_semantic(vector_results, alpha, k)
    scored_structured = score_structured(bm25_results, alpha, k)
    sem_dict = {r.chunk_id: r for r in scored_semantic}
    bm25_dict = {r.chunk_id: r for r in scored_structured}
    for chunk_id in set(sem_dict) | set(bm25_dict):
        sem = sem_dict.get(chunk_id)
        bm = bm25_dict.get(chunk_id)
        final = (sem.score if sem else 0.0) + (bm.score if bm else 0.0)
        if chunk_id in sem_dict:
            sem_dict[chunk_id].score = final
        else:
            bm25_dict[chunk_id].score = final
    ordered = sorted({**bm25_dict, **sem_dict}.values(), key=lambda r: r.score, reverse=True)
    for rank, result in enumerate(ordered, start=1):
        result.rank = rank
    return ordered


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
    alpha: float,
    collection_id: str,
    redis_client: Any,
) -> tuple[list[SearchResult], SearchMode]:
    """Re-rank vector candidates per ``mode``; fall back when the BM25 corpus is empty.

    SEMANTIC keeps the vector order. HYBRID fuses vector + BM25 via RRF. BM25 ranks
    the candidates purely by BM25 score. Both BM25-using modes degrade to
    SEMANTIC_ONLY (vector order) when the collection has no BM25 corpus yet.

    Args:
        candidates: Vector-store hits to re-rank.
        query: Raw query text for BM25 tokenisation.
        mode: Requested ranking mode.
        alpha: Semantic weight for HYBRID RRF.
        collection_id: Collection whose BM25 corpus to load.
        redis_client: Sync Redis client backing the BM25 corpus.

    Returns:
        Tuple of (ranked results, effective mode).
    """
    if mode == SearchMode.SEMANTIC:
        return candidates, SearchMode.SEMANTIC
    bm25_scores = _get_bm25_scores(redis_client, CorpusConfig(collection_id), query)
    if not bm25_scores:
        return candidates, SearchMode.SEMANTIC_ONLY
    if mode == SearchMode.BM25:
        # ponytail: BM25-only re-ranks the vector candidate set (same recall ceiling
        # as HYBRID). For unbounded keyword recall, rank the full Redis corpus instead.
        ranked = _rank_by_bm25(candidates, bm25_scores)
        for rank, result in enumerate(ranked, start=1):
            result.rank = rank
        return ranked, SearchMode.BM25
    fused = _reciprocal_rank_fusion(candidates, _rank_by_bm25(candidates, bm25_scores), alpha)
    return fused, SearchMode.HYBRID


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
    vector_store: VectorStoreAdapter,
    redis_client: Any,
    reranker: Reranker | None = None,
) -> tuple[list[SearchResult], SearchMode, int, int]:
    """Search a single collection and return ranked results.

    Args:
        collection_id: The collection to search.
        query_vector: Pre-computed embedding of the query.
        query: Raw query text (used for BM25 tokenisation).
        top_k: Maximum number of results to return after filtering.
        fan_out: Multiplier for pre-filter candidate retrieval (clamped to 1–10).
        mode: Ranking mode (HYBRID, SEMANTIC, or BM25).
        alpha: Semantic weight in RRF (passed through to score_semantic).
        filters: Optional metadata filters applied after ranking.
        vector_store: Adapter for vector similarity search.
        redis_client: Sync Redis client used to load the BM25 corpus.
        reranker: Optional cross-encoder; when set, reorders the over-fetched
            candidate pool by query-document relevance before the top_k cut.

    Returns:
        Tuple of (results, search_mode, retrieved_before_filter, returned_after_filter).
    """
    candidates = vector_store.search(
        collection_id, query_vector, top_k * min(max(fan_out, 1), 10)
    )
    results, effective_mode = _rank_candidates(
        candidates, query, mode, alpha, collection_id, redis_client
    )
    retrieved = len(results)
    filtered = apply_filters(results, filters)
    if reranker is not None:
        filtered = reranker.rerank(query, filtered)
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

    Each collection's results are already rank-ordered. Re-scoring every result
    by ``1 / (k + rank_within_collection)`` and globally sorting normalises the
    differing raw-score scales across backends (e.g. Chroma/pgvector
    ``1 - distance`` vs Qdrant's native similarity) so no single collection's
    score range dominates the merge. Results are copied via model_copy() so the
    per-collection originals are not mutated.

    Args:
        per_collection: One rank-ordered result list per collection.

    Returns:
        New, globally re-ranked list of copied SearchResult objects.
    """
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
    vector_store: VectorStoreAdapter,
    redis_client: Any,
    fan_out: int,
    reranker: Reranker | None = None,
) -> tuple[list[SearchResult], SearchMode, int, int]:
    """Thread target: run one collection's search (no DB access on this thread).

    Args:
        col_id: Collection to search.
        query_vector: Pre-computed query embedding.
        request: Full search request (top_k, hybrid, filters, etc.).
        vector_store: Vector similarity search adapter.
        redis_client: Redis client for the BM25 corpus.
        fan_out: Candidate multiplier applied before filtering.
        reranker: Optional cross-encoder reranker (skipped when None).

    Returns:
        (results, mode, retrieved_before_filter, returned_after_filter).
    """
    return search_collection(
        col_id, query_vector, request.query, request.top_k,
        fan_out=fan_out, mode=request.resolved_mode(), alpha=request.hybrid_alpha,
        filters=request.filters, vector_store=vector_store, redis_client=redis_client,
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


async def multi_collection_search(
    request: SearchRequest,
    *,
    db: AsyncSession,
    embedder: EmbeddingAdapter,
    vector_store: VectorStoreAdapter,
    redis_client: Any,
    reranker: Reranker | None = None,
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
        vector_store: Vector store adapter for similarity search.
        redis_client: Sync Redis client for BM25 corpus access.
        reranker: Optional cross-encoder reranker applied per collection before
            the cross-collection merge; ``None`` skips the stage (RRF-only).

    Returns:
        SearchResponse with ranked results, stats, and timing fields.
    """
    t0 = monotonic()
    query_vector = embedder.embed(request.query)
    embed_ms = int((monotonic() - t0) * 1000)
    fan_out = request.fan_out if request.fan_out is not None else _DEFAULT_FAN_OUT
    infos = await _get_collections_info(db, request.collection_ids)
    known = [cid for cid in request.collection_ids if cid in infos]
    outcomes = await asyncio.gather(*[
        asyncio.to_thread(
            _fan_out_one, cid, query_vector, request,
            vector_store=vector_store, redis_client=redis_client, fan_out=fan_out,
            reranker=reranker,
        )
        for cid in known
    ])
    per_collection, stats, fallback = _collect_results(known, list(outcomes), infos)
    mode = fallback or request.resolved_mode()
    final = _merge_collections_rrf(per_collection)[: request.top_k]
    _update_top_k_stats(final, stats)
    total_ms = int((monotonic() - t0) * 1000)
    return SearchResponse(
        results=final, collection_stats=stats, query_embedding_ms=embed_ms,
        search_ms=total_ms - embed_ms, total_ms=total_ms,
        search_mode=mode, under_delivered=len(final) < request.top_k,
    )
