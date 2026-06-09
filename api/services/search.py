from typing import Any

from api.services.bm25 import score_semantic, score_structured
from rank_bm25 import BM25Okapi

from api.models.redis import CorpusConfig
from api.models.search import SearchFilters, SearchResult
from api.services.logs import debug
from api.services.redis.redis import get_corpus, get_corpus_version

# Version-keyed in-process cache: collection_id → (version, index, doc_ids)
_bm25_cache: dict[str, tuple[int, BM25Okapi, list[str]]] = {}


def _get_cached(collection_id: str) -> tuple | None:
    return _bm25_cache.get(collection_id)


def _has_cached(cached: Any, version: int) -> bool:
    return cached is not None and cached[0] == version


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
        filter_tags = set(tags)

        if not filter_tags.issubset(result_tags):
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
        A mapping of document_id to BM25 score for every entry in the corpus.
        Returns an empty dict when the corpus is empty or unavailable.
    """
    version = get_corpus_version(redis_client, corpus_config)
    collection_id = corpus_config.collection_id

    cached = _get_cached(collection_id)
    if not _has_cached(cached, version):
        corpus = get_corpus(redis_client, corpus_config)
        if not corpus.data:
            return {}

        doc_ids = corpus.doc_ids
        tokenized = corpus.tokenized
        index = BM25Okapi(tokenized)
        _bm25_cache[collection_id] = (version, index, doc_ids)

        debug(
            "rebuilt BM25 index for collection %s at version %d (%d entries)",
            collection_id,
            version,
            len(doc_ids),
        )
    else:
        _, index, doc_ids = cached

    scores: list[float] = index.get_scores(query.lower().split()).tolist()
    return dict(zip(doc_ids, scores, strict=True))


def _reciprocal_rank_fusion(
    vector_results: list[SearchResult],
    bm25_results: list[SearchResult],
    alpha: float = 0.7,
    k: int = 60,
) -> list[SearchResult]:
    scored_semantic = score_semantic(vector_results, alpha, k)
    scored_structured = score_structured(bm25_results, alpha, k)

    scored_semantic_dict = {result.chunk_id: result for result in scored_semantic}
    scored_structured_dict = {result.chunk_id: result for result in scored_structured}

    all_chunk_ids = set(scored_semantic_dict.keys()) | set(scored_structured_dict.keys())
    for chunk_id in all_chunk_ids:
        semantic_chunk = scored_semantic_dict.get(chunk_id)
        structured_chunk = scored_structured_dict.get(chunk_id)

        semantic_score = semantic_chunk.score if semantic_chunk else 0
        structured_score = structured_chunk.score if structured_chunk else 0

        final_score = semantic_score + structured_score
        if chunk_id in scored_semantic_dict:
            scored_semantic_dict[chunk_id].score = final_score
        elif chunk_id in scored_structured_dict:
            scored_structured_dict[chunk_id].score = final_score

    final_dict = {**scored_structured_dict, **scored_semantic_dict}
    ordered = sorted(final_dict.values(), key=lambda r: r.score, reverse=True)
    ranked = enumerate(ordered, start=1)
    for rank, result in ranked:
        result.rank = rank
    return ordered


def search_collection() -> None:
    """Search in a single collection and return ranked results.

    Returns:
        None
    """


def multi_collection_search() -> None:
    """Search across multiple collections and merge the results.

    Returns:
        None
    """
