from api.models.search import SearchResult


def score_semantic(
    results: list[SearchResult], alpha: float = 0.7, k: int = 60
) -> list[SearchResult]:
    """Score search results based on semantic similarity.

    Args:
        results: A list of SearchResult objects containing vector similarity scores.
        alpha: Weighting factor for the vector similarity score (between 0 and 1).
        k: The number of top results to return after fusion.

    Returns:
        A list of SearchResult objects ranked by their fused scores.
    """
    
    # Create copies to avoid mutating original results
    results = [result.model_copy() for result in results]
    
    for rank, result in enumerate(results):
        rank += 1
        result.score = alpha * (1 / (k + rank))

    return sorted(results, key=lambda r: r.score, reverse=True)


def score_structured(
    results: list[SearchResult], alpha: float = 0.7, k: int = 60
) -> list[SearchResult]:
    """Score search results based on structured relevance.

    Args:
        results: A list of SearchResult objects containing structured relevance scores.
        alpha: Weighting factor for the structured relevance score (between 0 and 1).
    Returns:
        A list of SearchResult objects ranked by their structured relevance scores.
    """
    
    # Create copies to avoid mutating original results
    results = [result.model_copy() for result in results]
    
    for rank, result in enumerate(results):
        rank += 1
        result.score = (1 - alpha) * (1 / (k + rank))
    return sorted(results, key=lambda r: r.score, reverse=True)
