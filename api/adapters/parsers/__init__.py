"""Parser registry — maps a file extension to a configured parser adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from api.adapters.base import ParserAdapter

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig, ParserConfig

# Office formats handled only by the docling heavy path (no lightweight adapter). Gated on
# docling being the configured backend so an upload is rejected up front rather than failing
# later in the worker when docling's ML stack isn't set up.
DOCLING_EXTENSIONS: tuple[str, ...] = (".docx", ".pptx")


def _make_registry() -> dict[str, Callable[[ChunkingConfig | None], ParserAdapter]]:
    """Return a dict mapping each core extension to its lightweight parser factory."""
    from api.adapters.parsers.markdown import MarkdownParser
    from api.adapters.parsers.pdf import PDFParser
    from api.adapters.parsers.txt import TXTParser

    return {
        ".pdf": PDFParser,
        ".txt": TXTParser,
        ".md": MarkdownParser,
        ".markdown": MarkdownParser,
    }


# Built once at import time (all parsers are lightweight dataclass-like objects).
_REGISTRY: dict[str, Callable[[ChunkingConfig | None], ParserAdapter]] = _make_registry()

# Core formats always ingestible via the lightweight adapters — exactly the registry's keys, so
# the supported-extensions gate and get_parser's dispatch can never drift out of sync.
CORE_EXTENSIONS: frozenset[str] = frozenset(_REGISTRY)


def docling_configured(parsers: ParserConfig | None) -> bool:
    """True when the operator has explicitly opted into docling (``pdf_backend == "docling"``).

    Docling is the only backend for ``.docx``/``.pptx`` and an optional OCR/table backend for
    PDF; both need its heavy ML stack. Gating the office formats on this flag lets an upload be
    rejected at the API boundary when docling isn't configured, instead of the worker failing on
    a missing dependency after the file has already been accepted and enqueued.
    """
    return parsers is not None and parsers.pdf_backend == "docling"


def supported_extensions(parsers: ParserConfig | None = None) -> set[str]:
    """File extensions the ingestion pipeline accepts under ``parsers``.

    Always the core formats; the docling office formats (:data:`DOCLING_EXTENSIONS`) only when
    docling is configured. Callers validate uploads against this set.
    """
    exts = set(CORE_EXTENSIONS)
    if docling_configured(parsers):
        exts.update(DOCLING_EXTENSIONS)
    return exts


def _should_use_docling(ext: str, parsers: ParserConfig) -> bool:
    """Whether ``ext`` should be parsed by docling given the parser config."""
    if ext in DOCLING_EXTENSIONS:
        return True
    return ext == ".pdf" and docling_configured(parsers)


def _build_docling_parser(ext: str, parsers: ParserConfig) -> ParserAdapter:
    """Instantiate a :class:`DoclingParser` for ``ext`` (lazy heavy import)."""
    from api.adapters.parsers.docling_adapter import DoclingParser

    if ext in DOCLING_EXTENSIONS:
        # SimplePipeline path — fast, no OCR / layout model needed.
        return DoclingParser(
            device=parsers.docling_device, artifacts_path=parsers.docling_artifacts_path
        )
    return DoclingParser(
        ocr=parsers.docling_ocr,
        ocr_engine=parsers.docling_ocr_engine,
        table_structure=parsers.docling_tables,
        device=parsers.docling_device,
        flash_attention=parsers.docling_flash_attention,
        ocr_batch_size=parsers.docling_ocr_batch_size,
        layout_batch_size=parsers.docling_layout_batch_size,
        artifacts_path=parsers.docling_artifacts_path,
    )


def get_parser(
    file_extension: str,
    config: ChunkingConfig | None = None,
    *,
    parsers: ParserConfig | None = None,
) -> ParserAdapter:
    """Resolve the parser adapter for a given file extension.

    Args:
        file_extension: File extension including the dot (e.g. ``".pdf"``).
        config: The app's chunking config — tunes window sizes; when omitted each
            parser falls back to its built-in defaults.
        parsers: The app's parser config — selects the PDF backend
            (``pymupdf``/``docling``) and docling options, and gates the docling office
            formats. Defaults route every core extension to its lightweight adapter.

    Raises:
        ValueError: When ``file_extension`` is not supported under ``parsers`` (an unknown
            extension, or a docling office format while docling is not configured).
    """
    from api.models.config import ParserConfig

    ext = file_extension.lower()
    parser_cfg = parsers or ParserConfig()
    if ext not in supported_extensions(parser_cfg):
        raise ValueError(f"No parser registered for extension: {ext!r}")
    if _should_use_docling(ext, parser_cfg):
        return _build_docling_parser(ext, parser_cfg)
    return _REGISTRY[ext](config)
