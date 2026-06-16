from typing import Protocol, runtime_checkable

from api.models.chunk import Chunk
from api.models.document import DocumentSummary
from api.models.search import SearchResult
from api.models.tagging import TagSuggestion


@runtime_checkable
class ParserAdapter(Protocol):
    """Converts a file on disk into a list of text chunks."""

    def parse(self, file_path: str, document_id: str) -> list[Chunk]: ...

    def supported_extensions(self) -> list[str]: ...


@runtime_checkable
class EmbeddingAdapter(Protocol):
    """Converts text into dense embedding vectors."""

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    def dimensions(self) -> int: ...


@runtime_checkable
class VectorStoreAdapter(Protocol):
    """Stores and retrieves embedded chunks by collection namespace."""

    def ping(self) -> bool:
        """Return True if the backing store answers a liveness round-trip."""
        ...

    def upsert(
        self,
        collection_id: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> None: ...

    def search(
        self,
        collection_id: str,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[SearchResult]: ...

    def delete_document(self, collection_id: str, document_id: str) -> None: ...

    def delete_collection(self, collection_id: str) -> None: ...

    def list_documents(self, collection_id: str) -> list[DocumentSummary]: ...


@runtime_checkable
class TagSuggester(Protocol):
    """Proposes topical tags for a body of text.

    Implementations are swapped via ``config.yaml`` (``tagging.suggester``)
    with no router changes. ``suggest`` is synchronous (like EmbeddingAdapter)
    and is driven from a worker thread by the service layer.
    """

    def suggest(self, text: str, existing_tags: list[str]) -> list[TagSuggestion]: ...
