from typing import TYPE_CHECKING

from api.adapters.vector_store.pgvector import PgvectorAdapter

if TYPE_CHECKING:
    from api.models.config import VectorStoreConfig


def get_vector_store(config: "VectorStoreConfig", embedding_dimensions: int) -> PgvectorAdapter:
    return PgvectorAdapter(
        host=config.host,
        port=config.port,
        database=config.database,
        user=config.user,
        password=config.password,
        dimensions=embedding_dimensions,
        index_min_rows=config.index_min_rows,
    )
