"""Reranker registry — backend selected via config, returns None when disabled."""

from typing import TYPE_CHECKING

from api.adapters.base import Reranker

if TYPE_CHECKING:
    from api.models.config import RerankerConfig


def get_reranker(config: "RerankerConfig") -> Reranker | None:
    """Resolve the configured reranker, or ``None`` when disabled.

    Returning ``None`` (rather than a no-op object) lets the search service skip
    the stage entirely with a cheap ``is not None`` check.
    """
    if not config.enabled:
        return None

    if config.provider == "cross_encoder":
        from api.adapters.reranker.cross_encoder import CrossEncoderReranker
        return CrossEncoderReranker(model_name=config.model, top_n=config.top_n)

    raise ValueError(f"Unknown reranker provider: {config.provider!r}")
