from typing import TYPE_CHECKING

from api.adapters.base import VectorStoreAdapter
from api.adapters.vector_store import register

if TYPE_CHECKING:
    from api.models.config import VectorStoreConfig


@register("chroma")
def _chroma(config: "VectorStoreConfig", embedding_dimensions: int) -> VectorStoreAdapter:
    from api.adapters.vector_store.chroma import ChromaAdapter
    return ChromaAdapter(
        host=config.chroma.host,
        port=config.chroma.port,
    )


@register("pgvector")
def _pgvector(config: "VectorStoreConfig", embedding_dimensions: int) -> VectorStoreAdapter:
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


@register("qdrant")
def _qdrant(config: "VectorStoreConfig", embedding_dimensions: int) -> VectorStoreAdapter:
    from api.adapters.vector_store.qdrant import QdrantAdapter
    return QdrantAdapter(
        host=config.qdrant.host,
        port=config.qdrant.port,
        dimensions=embedding_dimensions,
    )
