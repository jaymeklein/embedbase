import warnings
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from api.constants import POSTGRES_PORT


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
    # Default: Ollama serving Google's embeddinggemma (2025) — a modern, compact,
    # multilingual retrieval model (768-dim). Requires a reachable Ollama with the
    # model pulled (`ollama pull embeddinggemma`). Switch provider to
    # "sentence_transformers" for a self-contained, in-process model.
    provider: str = "ollama"  # "ollama" | "sentence_transformers" | "openai_compat" | "gemini"
    model: str = "embeddinggemma"
    batch_size: int = 32
    base_url: str | None = None
    api_key: str | None = None
    concurrency: int = 8
    # Gemini only: truncate the (default 3072-dim) vector; the model re-normalises.
    output_dimensionality: int | None = None


class VectorStoreConfig(BaseModel):
    """Connection settings for the pgvector-backed chunk store (Postgres only)."""

    host: str = "postgres"
    port: int = POSTGRES_PORT
    database: str = "embedbase"
    user: str = "embedbase"
    password: str = ""
    index_min_rows: int = 100


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


class RerankerConfig(BaseModel):
    # Cross-encoder second-stage reranker. Reorders the over-fetched candidate
    # pool by true query-document relevance before the top_k cut — the biggest
    # precision win over RRF-only fusion. LLM-free: a local sentence-transformers
    # CrossEncoder, like the embedding model. Off by default so existing
    # deployments don't silently take on a model download + extra latency; flip
    # ``enabled`` to turn it on.
    enabled: bool = False
    provider: str = "cross_encoder"  # "cross_encoder"
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    top_n: int = 50  # max candidates scored per collection (caps cross-encoder cost)


class ParserConfig(BaseModel):
    # None = the user has never picked a backend; the UI then pre-selects one from
    # the detected GPU (docling on Ampere+, else pymupdf). Any saved value is honoured
    # as-is. None parses as pymupdf (see adapters/parsers/__init__.py).
    pdf_backend: str | None = None  # None (unset) | "pymupdf" | "docling"
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
    # Tag suggestion is LLM-only (no local/keyword backend); tagging is otherwise
    # manual. "llm" is the only supported value.
    backend: str = "llm"
    provider: str = "ollama"  # llm provider: "ollama" | "openai_compat"
    model: str = "llama3"
    base_url: str | None = None
    api_key: str | None = None
    max_tags: int = 8
    # Minimum confidence (0-1) a suggestion must meet to be auto-applied at
    # ingestion. Suggestions below this are dropped.
    min_confidence: float = 0.8


class TaggingConfig(BaseModel):
    suggester: TagSuggesterConfig = TagSuggesterConfig()
    # When true, the worker runs the suggester over each document at ingestion
    # and auto-applies tags scoring at least ``suggester.min_confidence``.
    auto_tag_on_ingest: bool = False


class LocalBackendConfig(BaseModel):
    """Files on the local/shared disk under ``settings.upload_dir`` (the default)."""

    type: Literal["local"] = "local"


class S3BackendConfig(BaseModel):
    """An S3-compatible target — AWS S3 or a self-hosted MinIO (``endpoint_url``).

    Credentials are kept out of ``config.yaml``: ``access_key_id`` /
    ``secret_access_key`` are overlaid from ``S3__<NAME>__*`` env vars (see
    :func:`api.services.config_env.overlay_storage_env`). Empty credentials mean
    "use boto3's default chain" (e.g. an AWS instance role).
    """

    type: Literal["s3"] = "s3"
    endpoint_url: str | None = None  # None = real AWS S3; set for MinIO/other
    public_endpoint_url: str | None = None  # host the browser reaches; signs presigns
    region: str = "us-east-1"
    bucket: str = "embedbase"
    access_key_id: str = ""
    secret_access_key: str = ""
    use_path_style: bool = True  # required by MinIO; harmless for AWS


# Discriminated on ``type`` so config.yaml entries validate to the right model.
Backend = Annotated[LocalBackendConfig | S3BackendConfig, Field(discriminator="type")]


class StorageConfig(BaseModel):
    """Named registry of object-storage backends; ``default`` gets new uploads."""

    default: str = "local"
    backends: dict[str, Backend] = {"local": LocalBackendConfig()}


class AppConfig(BaseModel):
    embedding: EmbeddingConfig = EmbeddingConfig()
    reranker: RerankerConfig = RerankerConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    parsers: ParserConfig = ParserConfig()
    search: SearchConfig = SearchConfig()
    mcp: MCPConfig = MCPConfig()
    tagging: TaggingConfig = TaggingConfig()
    storage: StorageConfig = StorageConfig()
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
