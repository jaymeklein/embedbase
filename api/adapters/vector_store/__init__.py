from collections.abc import Callable
from typing import TYPE_CHECKING

from api.adapters.base import VectorStoreAdapter

if TYPE_CHECKING:
    from api.models.config import VectorStoreConfig

_registry: dict[str, Callable[["VectorStoreConfig", int], VectorStoreAdapter]] = {}


def register(backend: str):
    def decorator(fn: Callable[["VectorStoreConfig", int], VectorStoreAdapter]):
        _registry[backend] = fn
        return fn
    return decorator


def get_vector_store(config: "VectorStoreConfig", embedding_dimensions: int) -> VectorStoreAdapter:
    builder = _registry.get(config.backend)
    if builder is None:
        raise ValueError(f"Unknown vector store backend: {config.backend!r}")
    return builder(config, embedding_dimensions)


from api.adapters.vector_store import backends  # noqa: F401, E402
