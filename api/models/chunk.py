import hashlib

from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    source_file: str
    filename: str
    parser: str  # pdf | txt | markdown
    document_id: str
    chunk_index: int

    # Optional, set by whichever parser can supply it (not parser-exclusive):
    page_number: int | None = None
    total_pages: int | None = None
    encoding: str | None = None
    char_count: int | None = None
    heading_path: str | None = None
    heading_level: int | None = None

    tags: list[str] = []


def make_chunk_id(document_id: str, chunk_index: int) -> str:
    """Deterministic, idempotent chunk ID — safe for upsert retries."""
    raw = f"{document_id}:{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()


class Chunk(BaseModel):
    id: str = Field(default="")
    text: str
    metadata: ChunkMetadata

    def model_post_init(self, __context: object) -> None:
        if not self.id:
            self.id = make_chunk_id(self.metadata.document_id, self.metadata.chunk_index)
