from enum import StrEnum

from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    language: str | None = None
    filename: str | None = None
    tags: list[str] | None = None


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    SEMANTIC = "semantic"
    BM25 = "bm25"
    SEMANTIC_ONLY = "semantic_only"  # response-only: hybrid/bm25 fell back (empty corpus)


class SearchRequest(BaseModel):
    query: str
    collection_ids: list[str] = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    # `mode` is the explicit selector; `hybrid` is kept for the MCP tool's bool API
    # and used only when `mode` is unset.
    mode: SearchMode | None = None
    hybrid: bool = True
    hybrid_alpha: float = Field(default=0.7, ge=0.0, le=1.0)
    fan_out: int | None = None
    filters: SearchFilters | None = None

    def resolved_mode(self) -> "SearchMode":
        """Effective request mode: explicit ``mode`` wins, else the ``hybrid`` bool."""
        if self.mode is not None:
            return self.mode
        return SearchMode.HYBRID if self.hybrid else SearchMode.SEMANTIC


class SourceProvenance(BaseModel):
    collection_id: str
    collection_name: str
    workspace_id: str
    workspace_name: str
    document_id: str | None = None
    filename: str | None = None
    page_number: int | None = None


class SearchResult(BaseModel):
    chunk_id: str
    text: str
    score: float
    rank: int = 0
    source: SourceProvenance | None = None
    metadata: dict = {}


class CollectionStat(BaseModel):
    name: str
    workspace_name: str
    retrieved_before_filter: int = 0
    returned_after_filter: int = 0
    contributed_to_top_k: int = 0


class SearchResponse(BaseModel):
    results: list[SearchResult]
    collection_stats: dict[str, CollectionStat] = {}
    query_embedding_ms: int = 0
    search_ms: int = 0
    total_ms: int = 0
    search_mode: SearchMode = SearchMode.HYBRID
    under_delivered: bool = False
