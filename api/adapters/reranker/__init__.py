"""Reranker registry — backend selected via config, returns None when disabled."""

from typing import TYPE_CHECKING

from api.adapters.base import Reranker

if TYPE_CHECKING:
    from api.models.config import RerankerConfig


def get_reranker(config: "RerankerConfig") -> Reranker | None:
    """Resolve the configured reranker, or ``None`` when disabled.

    Returning ``None`` (rather than a no-op object) lets the search service skip
    the stage entirely with a cheap ``is not None`` check. Dispatches on
    ``config.provider`` (lazy imports per branch); a remote provider missing its
    credentials raises ``ValueError`` so the config-save dry-run surfaces it as a
    422 rather than silently saving a reranker that would fail on the first query.
    """
    if not config.enabled:
        return None

    if config.provider == "cross_encoder":
        from api.adapters.reranker.cross_encoder import CrossEncoderReranker
        return CrossEncoderReranker(model_name=config.model, top_n=config.top_n)

    if config.provider == "rerank_api":
        from api.adapters.reranker.rerank_api import RerankApiReranker
        return RerankApiReranker(
            model=config.model,
            api_key=config.api_key or "",
            base_url=config.base_url or "",
            top_n=config.top_n,
            timeout=config.timeout_seconds,
        )

    if config.provider == "llm":
        from api.adapters.reranker.llm import LLMReranker
        return LLMReranker(
            llm_provider=config.llm_provider,
            model=config.model,
            base_url=config.base_url,
            api_key=config.api_key,
            top_n=config.top_n,
            timeout=config.timeout_seconds,
        )

    raise ValueError(f"Unknown reranker provider: {config.provider!r}")
