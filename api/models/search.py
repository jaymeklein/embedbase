from pydantic import BaseModel, Field


class SearchFilters(BaseModel):
    language: str | None = None
    filename: str | None = None
    tags: list[str] | None = None


class SearchRequest(BaseModel):
    query: str
    collection_ids: list[str] = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    hybrid: bool = True
    hybrid_alpha: float = Field(default=0.7, ge=0.0, le=1.0)
    fan_out: int | None = None
    filters: SearchFilters | None = None


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
    search_mode: str = "hybrid"  # hybrid | semantic | semantic_only
    under_delivered: bool = False
