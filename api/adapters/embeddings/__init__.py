from typing import TYPE_CHECKING
from api.adapters.base import EmbeddingAdapter

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

    raise ValueError(f"Unknown embedding provider: {provider!r}")
