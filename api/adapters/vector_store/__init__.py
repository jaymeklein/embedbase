from typing import TYPE_CHECKING

from api.adapters.base import VectorStoreAdapter

if TYPE_CHECKING:
    from api.models.config import VectorStoreConfig


def get_vector_store(config: "VectorStoreConfig", embedding_dimensions: int) -> VectorStoreAdapter:
    """Resolve and instantiate the configured vector store adapter."""
    backend = config.backend

    if backend == "chroma":
        from api.adapters.vector_store.chroma import ChromaAdapter
        return ChromaAdapter(
            host=config.chroma.host,
            port=config.chroma.port,
        )

    if backend == "pgvector":
        from api.adapters.vector_store.pgvector import PgvectorAdapter
        return PgvectorAdapter(
            host=config.pgvector.host,
            port=config.pgvector.port,
            database=config.pgvector.database,
            user=config.pgvector.user,
            password=config.pgvector.password,
            dimensions=embedding_dimensions,
            index_min_rows=config.pgvector.index_min_rows,
        )

    if backend == "qdrant":
        from api.adapters.vector_store.qdrant import QdrantAdapter
        return QdrantAdapter(
            host=config.qdrant.host,
            port=config.qdrant.port,
            dimensions=embedding_dimensions,
        )

    raise ValueError(f"Unknown vector store backend: {backend!r}")
