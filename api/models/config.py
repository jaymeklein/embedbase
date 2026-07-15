import warnings
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from api.constants import DEFAULT_EXPAND_CHAR_BUDGET, POSTGRES_PORT, RERANKER_MODEL


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
    # Client-side rate limit for an external embedding provider: the max number of
    # texts embedded per minute. During ingestion the worker throttles to stay under
    # this so bulk work runs continuously just below the provider's quota instead of
    # bursting into 429s and stalling. 0 = unlimited (default); set e.g. 90 for a
    # provider capped at 100/min. Each text counts as one request — the unit providers
    # like the Gemini free tier rate-limit on.
    max_rpm: int = 0


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


class ChunkingConfig(BaseModel):
    sliding_window: SlidingWindowConfig = SlidingWindowConfig()


class SearchConfig(BaseModel):
    default_top_k: int = 5
    max_top_k: int = 20
    retrieval_fan_out: int = 4
    max_fan_out: int = 10
    hybrid_default_alpha: float = 0.7
    # A2 adjacency expansion: after the top_k cut, pull this many chunks on EACH side of every
    # hit (by deterministic chunk-id) and coalesce contiguous runs into one span — so a context
    # that spilled past top_k onto the next page(s) comes back whole. Deterministic key lookup;
    # no embeddings, no LLM. On by default; 0 = off. A fetch error degrades to the un-expanded
    # hits (never a 500).
    expand_neighbors: int = 1
    max_expand_neighbors: int = 5  # UI/clamp guard for expand_neighbors
    expand_char_budget: int = DEFAULT_EXPAND_CHAR_BUDGET  # cap on an assembled span's text

    @property
    def effective_expand_neighbors(self) -> int:
        """``expand_neighbors`` clamped to ``[0, max_expand_neighbors]`` — the value search uses."""
        return max(0, min(self.expand_neighbors, self.max_expand_neighbors))


class RerankerConfig(BaseModel):
    # Second-stage reranker: reorders the over-fetched candidate pool by true
    # query-document relevance before the top_k cut — the biggest precision win over
    # RRF-only fusion. On by default; a build/load failure degrades to RRF-only
    # ranking (never a 500), so enabling it is safe.
    #
    # The stage is provider-pluggable, mirroring the embedding adapter:
    #   - "cross_encoder": local sentence-transformers CrossEncoder (LLM-free). The
    #     default model (~80MB) is baked into the api image and loaded from disk, so
    #     it needs no HuggingFace access at runtime (see api/adapters/reranker). A
    #     non-vendored model still loads from the Hub.
    #   - "rerank_api": a hosted /rerank endpoint (Cohere / Jina / Voyage). ``base_url``
    #     is the full endpoint; ``api_key`` is the bearer token.
    #   - "llm": LLM-as-reranker (pointwise relevance scoring) over a chat model, with
    #     ``llm_provider`` selecting the backend. Heavier — practical only against a
    #     fast remote endpoint.
    # Set ``enabled: false`` to disable the stage entirely.
    enabled: bool = True
    provider: str = "cross_encoder"  # "cross_encoder" | "rerank_api" | "llm"
    model: str = RERANKER_MODEL
    # Chat backend for the "llm" provider only (ignored otherwise).
    llm_provider: str = "gemini"  # "ollama" | "openai_compat" | "gemini"
    base_url: str | None = None  # rerank_api endpoint / llm base URL (blank = provider default)
    api_key: str | None = None  # bearer token for the hosted/LLM provider (secret; masked on GET)
    top_n: int = 50  # max candidates scored per collection (caps rerank cost)
    timeout_seconds: float = 60.0  # per-request timeout for the remote providers


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
    # Lifetime of a *temporary* upload (ingested with ``temporary=true``): after
    # this many hours the worker sweep purges its object, row, chunks, and vectors.
    # 0 = feature off (a ``temporary`` upload then just behaves as permanent).
    # ponytail: whole-hours granularity. If sub-hour temp files are ever needed,
    # switch the unit — don't add a units system.
    temp_retention_hours: int = 0


class AppConfig(BaseModel):
    embedding: EmbeddingConfig = EmbeddingConfig()
    reranker: RerankerConfig = RerankerConfig()
    vector_store: VectorStoreConfig = VectorStoreConfig()
    chunking: ChunkingConfig = ChunkingConfig()
    parsers: ParserConfig = ParserConfig()
    search: SearchConfig = SearchConfig()
    mcp: MCPConfig = MCPConfig()
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
