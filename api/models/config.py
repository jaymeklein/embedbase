import warnings
from typing import Any

from pydantic import BaseModel


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
    port: int = 8001


class PgvectorConfig(BaseModel):
    host: str = "postgres"
    port: int = 5432
    database: str = "embedbase"
    user: str = "embedbase"
    password: str = ""
    index_min_rows: int = 100


class QdrantConfig(BaseModel):
    host: str = "qdrant"
    port: int = 6333


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


class MCPConfig(BaseModel):
    enabled: bool = True
    rate_limit_rpm: int = 60
    max_results: int = 20


class LoggingConfig(BaseModel):
    level: str = "info"
    format: str = "json"


class AppConfig(BaseModel):
    embedding: EmbeddingConfig = EmbeddingConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    search: SearchConfig = SearchConfig()
    mcp: MCPConfig = MCPConfig()
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def model_validate(cls, obj: Any, /, **kwargs: Any) -> "AppConfig":  # type: ignore[override]
        if isinstance(obj, dict):
            _warn_extra_keys(obj, cls)
        return super().model_validate(obj, **kwargs)
