"""Hosted reranker via a provider's ``/rerank`` endpoint (Cohere / Jina / Voyage)."""

from __future__ import annotations

from api.adapters.llm_chat import post_json
from api.adapters.reranker.reorder import rerank_by_scores
from api.models.search import SearchResult


class RerankApiReranker:
    """Reorders candidates with a hosted ``/rerank`` API (Reranker Protocol).

    Sends the query + candidate texts to a purpose-built rerank endpoint and reads back a
    relevance score per candidate. The request/response shape is shared by Cohere, Jina, and
    Voyage: ``{model, query, documents: [str]}`` → ``{"results" | "data": [{index,
    relevance_score}]}``. ``top_n`` is intentionally omitted from the request so every candidate
    is scored (the ``top_n`` cut is applied by :func:`rerank_by_scores`, consistently with the
    other providers).

    ``base_url`` is the **full** endpoint (e.g. ``https://api.cohere.com/v2/rerank``) because the
    path differs per vendor. A network/HTTP error degrades to the pre-rerank order (see reorder).
    """

    def __init__(
        self, model: str, api_key: str, base_url: str, top_n: int = 50, timeout: float = 60.0
    ) -> None:
        if not api_key.strip():
            raise ValueError("rerank_api provider requires an api_key")
        if not base_url.strip():
            raise ValueError("rerank_api provider requires a base_url (the full /rerank endpoint)")
        self._model = model
        self._api_key = api_key
        self._url = base_url.rstrip("/")
        self._top_n = max(1, top_n)
        self._timeout = timeout

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        return rerank_by_scores(query, results, self._top_n, self._score, provider="rerank_api")

    def _score(self, query: str, texts: list[str]) -> list[float]:
        body = {"model": self._model, "query": query, "documents": texts}
        data = post_json(
            self._url, body, {"Authorization": f"Bearer {self._api_key}"}, self._timeout
        )
        items = data.get("results") or data.get("data") or []
        scores = [0.0] * len(texts)
        for item in items:
            index = item.get("index")
            if isinstance(index, int) and 0 <= index < len(texts):
                # An explicit JSON null must not become float(None); leave the 0.0 default. A
                # non-finite/garbage number is coerced/sanitised downstream in reorder.
                raw = item.get("relevance_score")
                if raw is None:
                    raw = item.get("score")
                if raw is not None:
                    scores[index] = float(raw)
        return scores
