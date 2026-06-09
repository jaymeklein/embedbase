"""Search service: BM25 helpers, single-collection search, multi-collection fan-out."""

from time import monotonic
from typing import Any

from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.base import EmbeddingAdapter, VectorStoreAdapter
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


def search_collection(
    collection_id: str,
    query_vector: list[float],
    query: str,
    top_k: int,
    *,
    fan_out: int = _DEFAULT_FAN_OUT,
    hybrid: bool = True,
    alpha: float = 0.7,
    filters: SearchFilters | None = None,
    vector_store: VectorStoreAdapter,
    redis_client: Any,
) -> tuple[list[SearchResult], SearchMode, int, int]:
    """Search a single collection and return ranked results.

    Args:
        collection_id: The collection to search.
        query_vector: Pre-computed embedding of the query.
        query: Raw query text (used for BM25 tokenisation).
        top_k: Maximum number of results to return after filtering.
        fan_out: Multiplier for pre-filter candidate retrieval (clamped to 1–10).
        hybrid: Whether to combine semantic and BM25 rankings via RRF.
        alpha: Semantic weight in RRF (passed through to score_semantic).
        filters: Optional metadata filters applied after ranking.
        vector_store: Adapter for vector similarity search.
        redis_client: Sync Redis client used to load the BM25 corpus.

    Returns:
        Tuple of (results, search_mode, retrieved_before_filter, returned_after_filter).
    """
    candidates = vector_store.search(
        collection_id, query_vector, top_k * min(max(fan_out, 1), 10)
    )
    mode = SearchMode.SEMANTIC if not hybrid else SearchMode.HYBRID
    results: list[SearchResult] = candidates
    if hybrid:
        bm25_scores = _get_bm25_scores(redis_client, CorpusConfig(collection_id), query)
        if bm25_scores:
            results = _reciprocal_rank_fusion(candidates, _rank_by_bm25(candidates, bm25_scores), alpha)
        else:
            mode = SearchMode.SEMANTIC_ONLY
    retrieved = len(results)
    filtered = apply_filters(results, filters)
    return filtered[:top_k], mode, retrieved, len(filtered)


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


def _merge_and_rank(all_results: list[SearchResult]) -> list[SearchResult]:
    """Return a new list sorted by score descending with sequential ranks assigned.

    Copies each result via model_copy() so the originals are not mutated.

    Args:
        all_results: Results from one or more collections.

    Returns:
        New sorted list of copied SearchResult objects with rank fields set.
    """
    ordered = sorted(
        (r.model_copy() for r in all_results), key=lambda r: r.score, reverse=True
    )
    for rank, r in enumerate(ordered, start=1):
        r.rank = rank
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


async def _get_collection_info(db: AsyncSession, col_id: str) -> dict[str, str] | None:
    """Fetch collection name and workspace metadata from the database.

    Args:
        db: Active async database session.
        col_id: Collection UUID to look up.

    Returns:
        Dict with collection_name, workspace_id, workspace_name, or None if not found.
    """
    from api.db import collections as col_t
    from api.db import workspaces as ws_t

    row = (
        await db.execute(
            select(col_t.c.name, col_t.c.workspace_id, ws_t.c.name.label("workspace_name"))
            .join(ws_t, col_t.c.workspace_id == ws_t.c.id)
            .where(col_t.c.id == col_id)
        )
    ).fetchone()
    if row is None:
        return None
    return {
        "collection_name": str(row.name),
        "workspace_id": str(row.workspace_id),
        "workspace_name": str(row.workspace_name),
    }


async def _fan_out_to_collection(
    col_id: str,
    query_vector: list[float],
    request: SearchRequest,
    *,
    db: AsyncSession,
    vector_store: VectorStoreAdapter,
    redis_client: Any,
    fan_out: int,
) -> tuple[list[SearchResult], CollectionStat, SearchMode] | None:
    """Search one collection, annotate provenance, and return results + stat.

    Args:
        col_id: Collection UUID to search.
        query_vector: Pre-computed query embedding.
        request: Full search request (top_k, hybrid, filters, etc.).
        db: Async database session for metadata lookup.
        vector_store: Vector similarity search adapter.
        redis_client: Redis client for BM25 corpus.
        fan_out: Candidate multiplier applied before filtering.

    Returns:
        (annotated_results, stat, mode) or None if collection not found.
    """
    info = await _get_collection_info(db, col_id)
    if info is None:
        return None
    col_results, col_mode, retrieved, returned = search_collection(
        col_id, query_vector, request.query, request.top_k,
        fan_out=fan_out, hybrid=request.hybrid, alpha=request.hybrid_alpha,
        filters=request.filters, vector_store=vector_store, redis_client=redis_client,
    )
    _apply_provenance(col_results, col_id, info)
    stat = CollectionStat(
        name=info["collection_name"], workspace_name=info["workspace_name"],
        retrieved_before_filter=retrieved, returned_after_filter=returned,
    )
    return col_results, stat, col_mode


async def multi_collection_search(
    request: SearchRequest,
    *,
    db: AsyncSession,
    embedder: EmbeddingAdapter,
    vector_store: VectorStoreAdapter,
    redis_client: Any,
) -> SearchResponse:
    """Search across one or more collections and merge results.

    Embeds the query once, fans out to each requested collection via
    _fan_out_to_collection, merges results with a global score sort, and
    computes per-collection stats.

    Args:
        request: Parsed SearchRequest from the caller.
        db: Async database session for metadata look-ups.
        embedder: Embedding adapter used to vectorise the query.
        vector_store: Vector store adapter for similarity search.
        redis_client: Sync Redis client for BM25 corpus access.

    Returns:
        SearchResponse with ranked results, stats, and timing fields.
    """
    t0 = monotonic()
    query_vector = embedder.embed(request.query)
    embed_ms = int((monotonic() - t0) * 1000)
    fan_out = request.fan_out if request.fan_out is not None else _DEFAULT_FAN_OUT
    all_results: list[SearchResult] = []
    stats: dict[str, CollectionStat] = {}
    mode = SearchMode.HYBRID if request.hybrid else SearchMode.SEMANTIC
    for col_id in request.collection_ids:
        hit = await _fan_out_to_collection(
            col_id, query_vector, request,
            db=db, vector_store=vector_store, redis_client=redis_client, fan_out=fan_out,
        )
        if hit is None:
            continue
        col_results, stat, col_mode = hit
        if col_mode == SearchMode.SEMANTIC_ONLY:
            mode = SearchMode.SEMANTIC_ONLY
        stats[col_id] = stat
        all_results.extend(col_results)
    final = _merge_and_rank(all_results)[:request.top_k]
    _update_top_k_stats(final, stats)
    total_ms = int((monotonic() - t0) * 1000)
    return SearchResponse(
        results=final, collection_stats=stats, query_embedding_ms=embed_ms,
        search_ms=total_ms - embed_ms, total_ms=total_ms,
        search_mode=mode, under_delivered=len(final) < request.top_k,
    )
