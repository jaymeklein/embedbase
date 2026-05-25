import hashlib

from pydantic import BaseModel, Field


class ChunkMetadata(BaseModel):
    source_file: str
    filename: str
    parser: str  # pdf | txt | markdown | code | csv | json
    document_id: str
    chunk_index: int

    # PDF
    page_number: int | None = None
    total_pages: int | None = None
    # TXT
    encoding: str | None = None
    char_count: int | None = None
    # Markdown
    heading_path: str | None = None
    heading_level: int | None = None
    # Code
    language: str | None = None
    symbol_name: str | None = None
    symbol_type: str | None = None  # function | class | method
    start_line: int | None = None
    end_line: int | None = None
    # CSV
    columns: list[str] | None = None
    row_range: str | None = None
    total_rows: int | None = None
    # JSON
    json_shape: str | None = None  # array | object
    depth: int | None = None
    key_count: int | None = None

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
