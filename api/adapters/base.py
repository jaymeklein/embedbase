from typing import Protocol, runtime_checkable

from api.models.chunk import Chunk
from api.models.document import DocumentSummary
from api.models.search import SearchResult
from api.models.tagging import TagSuggestion


@runtime_checkable
class ParserAdapter(Protocol):
    """Converts a file on disk into a list of text chunks."""

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        """Parse ``file_path`` into chunks.

        A parser MAY additionally accept an ``on_progress(current, total)`` keyword
        to report progress — the PDF parser does, and the worker passes it only when
        present (see ``worker._parse_with_progress``). It's intentionally kept off
        this Protocol so parsers that don't support it still conform.
        """
        ...

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

    def iter_document_chunks(
        self, collection_id: str, document_id: str
    ) -> list[tuple[str, str, str]]:
        """Return ``(chunk_id, document_id, text)`` triples for a document's chunks.

        Lets the BM25 corpus be rebuilt straight from the vector store (which
        already stores the chunk text) without re-parsing or re-embedding the
        original file. Returns an empty list when the collection or document is
        absent.
        """
        ...

    def delete_document(self, collection_id: str, document_id: str) -> None: ...

    def delete_collection(self, collection_id: str) -> None: ...

    def list_documents(self, collection_id: str) -> list[DocumentSummary]: ...

    def set_document_tags(
        self, collection_id: str, document_id: str, tags: list[str]
    ) -> None:
        """Replace the ``tags`` metadata on every stored chunk of a document.

        The search bridge (D6) calls this when a document's effective tags
        change so D3 ``apply_filters`` tag filtering returns the right chunks.
        """
        ...


@runtime_checkable
class Reranker(Protocol):
    """Re-orders candidate search results by joint query-document relevance.

    A second-stage cross-encoder: unlike the vector/BM25 scores (computed
    independently per side), ``rerank`` scores the query and each candidate's
    text *together*. ``rerank`` is synchronous (like EmbeddingAdapter) and is
    driven from a worker thread by the search service.
    """

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]: ...


@runtime_checkable
class TagSuggester(Protocol):
    """Proposes topical tags for a body of text.

    Implementations are swapped via ``config.yaml`` (``tagging.suggester``)
    with no router changes. ``suggest`` is synchronous (like EmbeddingAdapter)
    and is driven from a worker thread by the service layer.
    """

    def suggest(self, text: str, existing_tags: list[str]) -> list[TagSuggestion]: ...
