from typing import Protocol, runtime_checkable

from api.models.chunk import Chunk
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
