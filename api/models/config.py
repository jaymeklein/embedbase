import warnings
from typing import Any

from pydantic import BaseModel

from api.constants import CHROMA_PORT, POSTGRES_PORT, QDRANT_PORT


def _warn_extra_keys(data: dict[str, Any], model_cls: type[BaseModel], prefix: str = "") -> None:
    known = set(model_cls.model_fields)
    for key in data:
        full = f"{prefix}.{key}" if prefix else key

        if key not in known:
            warnings.warn(
                f"config.yaml: unknown key '{full}' will be ignored", UserWarning, stacklevel=5
            )

        elif isinstance(data[key], dict):
            ann = model_cls.model_fields[key].annotation

            if isinstance(ann, type) and issubclass(ann, BaseModel):
                _warn_extra_keys(data[key], ann, full)


class EmbeddingConfig(BaseModel):
    provider: str = "sentence_transformers"
    model: str = "all-MiniLM-L6-v2"
    batch_size: int = 32
    base_url: str | None = None
    api_key: str | None = None
    concurrency: int = 8


class ChromaConfig(BaseModel):
    host: str = "chroma"
    port: int = CHROMA_PORT
    auth_token: str = "embedbase-internal"


class PgvectorConfig(BaseModel):
    host: str = "postgres"
    port: int = POSTGRES_PORT
    database: str = "embedbase"
    user: str = "embedbase"
    password: str = ""
    index_min_rows: int = 100


class QdrantConfig(BaseModel):
    host: str = "qdrant"
    port: int = QDRANT_PORT


class VectorStoreConfig(BaseModel):
    backend: str = "chroma"
    chroma: ChromaConfig = ChromaConfig()
    pgvector: PgvectorConfig = PgvectorConfig()
    qdrant: QdrantConfig = QdrantConfig()


class SlidingWindowConfig(BaseModel):
    max_tokens: int = 512
    overlap_tokens: int = 64


class CsvChunkConfig(BaseModel):
    rows_per_chunk: int = 10


class CodeChunkConfig(BaseModel):
    max_symbol_tokens: int = 4096
    fallback_window_lines: int = 50


class ChunkingConfig(BaseModel):
    sliding_window: SlidingWindowConfig = SlidingWindowConfig()
    csv: CsvChunkConfig = CsvChunkConfig()
    code: CodeChunkConfig = CodeChunkConfig()


class SearchConfig(BaseModel):
    default_top_k: int = 5
    max_top_k: int = 20
    retrieval_fan_out: int = 4
    max_fan_out: int = 10
    hybrid_default_alpha: float = 0.7
    bm25_cache_ttl: int = 60


class ParserConfig(BaseModel):
    pdf_backend: str = "pymupdf"  # "pymupdf" | "docling"
    docling_ocr: bool = False  # enable OCR for scanned pages
    docling_ocr_engine: str = "easyocr"  # "easyocr" | "tesseract" | "rapidocr"
    docling_tables: bool = True  # table structure recognition
    # GPU acceleration — "auto" (default) detects the GPU and configures the
    # device, flash attention, and batch sizes automatically; no env/config flags
    # needed. Set "cpu"/"cuda" to pin a device and the knobs below explicitly.
    docling_device: str = "auto"  # "auto" | "cpu" | "cuda"
    docling_flash_attention: bool = False  # ignored under "auto"; Ampere-only (RTX 30/40)
    docling_ocr_batch_size: int = 8  # ignored under "auto" (detection picks 64 on GPU)
    docling_layout_batch_size: int = 8  # ignored under "auto" (detection picks 64 on GPU)
    # Local directory holding the docling layout/OCR/table models. When set, docling
    # loads models from here instead of the default HuggingFace cache — pin it for
    # offline/air-gapped runs or a mounted models volume. Overridable via the
    # DOCLING_ARTIFACTS_PATH env var (.env). None -> docling's default cache.
    docling_artifacts_path: str | None = None


class MCPConfig(BaseModel):
    enabled: bool = True
    rate_limit_rpm: int = 60
    max_results: int = 20


class TagSuggesterConfig(BaseModel):
    backend: str = "keyword"  # "keyword" (local) | "llm"
    provider: str = "ollama"  # llm provider: "ollama" | "openai_compat"
    model: str = "llama3"
    base_url: str | None = None
    api_key: str | None = None
    max_tags: int = 8


class TaggingConfig(BaseModel):
    suggester: TagSuggesterConfig = TagSuggesterConfig()


class AppConfig(BaseModel):
    embedding: EmbeddingConfig = EmbeddingConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    parsers: ParserConfig = ParserConfig()
    search: SearchConfig = SearchConfig()
    mcp: MCPConfig = MCPConfig()
    tagging: TaggingConfig = TaggingConfig()
    # Upload size cap (app-domain, editable via the config page). Distinct from
    # deploy/bootstrap config, which stays in .env.
    max_file_size_mb: int = 50

    @property
    def max_file_size_bytes(self) -> int:
        """The upload cap in bytes (``max_file_size_mb`` × 1 MiB)."""
        return self.max_file_size_mb * 1024 * 1024

    @classmethod
    def model_validate(cls, obj: Any, /, **kwargs: Any) -> "AppConfig":  # type: ignore[override]
        if isinstance(obj, dict):
            _warn_extra_keys(obj, cls)
        return super().model_validate(obj, **kwargs)
