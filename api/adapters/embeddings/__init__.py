from typing import TYPE_CHECKING

from api.adapters.base import EmbeddingAdapter
from api.adapters.embeddings.errors import RateLimitError

if TYPE_CHECKING:
    from api.models.config import EmbeddingConfig


def get_embedding_adapter(config: "EmbeddingConfig") -> EmbeddingAdapter:
    """Resolve and instantiate the configured embedding adapter."""
    provider = config.provider

    if provider == "sentence_transformers":
        from api.adapters.embeddings.sentence_transformers import SentenceTransformersAdapter
        return SentenceTransformersAdapter(model_name=config.model)

    if provider == "ollama":
        from api.adapters.embeddings.ollama import OllamaAdapter
        return OllamaAdapter(
            base_url=config.base_url or "http://host.docker.internal:11434",
            model=config.model,
            concurrency=config.concurrency,
        )

    if provider == "openai_compat":
        from api.adapters.embeddings.openai_compat import OpenAICompatAdapter
        return OpenAICompatAdapter(
            base_url=config.base_url or "http://host.docker.internal:1234",
            model=config.model,
            api_key=config.api_key or "",
        )

    if provider == "gemini":
        from api.adapters.embeddings.gemini import GeminiAdapter
        return GeminiAdapter(
            model=config.model,
            api_key=config.api_key or "",
            base_url=config.base_url or "https://generativelanguage.googleapis.com",
            output_dimensionality=config.output_dimensionality,
        )

    raise ValueError(f"Unknown embedding provider: {provider!r}")


def same_embedding_shape(a: "EmbeddingConfig", b: "EmbeddingConfig") -> bool:
    """Whether two embedding configs yield the same vector dimension, so a re-probe is needless.

    The dimension is fixed by provider + model + any output-dimensionality truncation, plus the
    base URL (the same model *name* on a different server can be a different model). The rate limit,
    API key, batch size, and concurrency don't change it — so an edit touching only those keeps the
    dimension, and the vector store need not be re-sized from a fresh provider probe.
    """
    return (a.provider, a.model, a.base_url, a.output_dimensionality) == (
        b.provider,
        b.model,
        b.base_url,
        b.output_dimensionality,
    )


def resolve_dimensions(
    embed: EmbeddingAdapter,
    new_config: "EmbeddingConfig",
    prior_config: "EmbeddingConfig | None",
    prior_dimensions: int | None,
) -> int:
    """The embedding vector size (to size the vector store), tolerant of a rate-limited provider.

    Probing the provider for the size (``embed.dimensions``) also confirms it is reachable and the
    credentials work, and re-learns the size if it ever changed — so the probe runs first and a bad
    key / unreachable host still fails fast. But a rate limit (HTTP 429) is transient, not an invalid
    config: when it strikes and the embedding model shape is unchanged, the size cannot have changed,
    so reuse the prior (still-live) store's known dimension instead of failing. That is what lets an
    unrelated edit — notably raising ``max_rpm`` to relieve the 429 — go through while the provider
    is throttled. Only a genuine model change while throttled truly can't be sized; that re-raises
    for the caller to surface as a transient error (a 503 in the API apply, an error ack + rollback
    in the worker reload).

    Shared by both config-apply call sites (:func:`api.services.config_service._build_adapters` and
    :func:`worker.tasks.reload_adapters`) so the API and every worker size the store identically.
    """
    try:
        return embed.dimensions
    except RateLimitError:
        if (
            prior_dimensions is not None
            and prior_config is not None
            and same_embedding_shape(new_config, prior_config)
        ):
            return prior_dimensions
        raise
