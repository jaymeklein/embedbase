from api.models.chunk import Chunk
from api.models.search import SearchResult
from api.models.document import DocumentSummary


class PgvectorAdapter:
    """Implemented in Delivery 3."""

    def __init__(self, host: str, port: int, database: str, user: str,
                 password: str, dimensions: int, index_min_rows: int = 100) -> None:
        self._dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        self._dimensions = dimensions
        self._index_min_rows = index_min_rows

    def upsert(self, collection_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        raise NotImplementedError("pgvector adapter implemented in Delivery 3")

    def search(self, collection_id: str, vector: list[float], top_k: int,
               filters: dict | None = None) -> list[SearchResult]:
        raise NotImplementedError

    def delete_document(self, collection_id: str, document_id: str) -> None:
        raise NotImplementedError

    def delete_collection(self, collection_id: str) -> None:
        raise NotImplementedError

    def list_documents(self, collection_id: str) -> list[DocumentSummary]:
        raise NotImplementedError
