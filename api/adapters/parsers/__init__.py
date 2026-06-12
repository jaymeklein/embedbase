"""Parser registry — maps a file extension to a configured parser adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from api.adapters.base import ParserAdapter

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig, ParserConfig

# Extensions handled only by the docling heavy path (no lightweight adapter).
DOCLING_EXTENSIONS: tuple[str, ...] = (".docx", ".pptx")


def _make_registry() -> dict[str, Callable[[ChunkingConfig | None], ParserAdapter]]:
    """Return a dict mapping each supported extension to its parser factory."""
    from api.adapters.parsers.code import CodeParser
    from api.adapters.parsers.csv_parser import CSVParser
    from api.adapters.parsers.json_parser import JSONParser
    from api.adapters.parsers.markdown import MarkdownParser
    from api.adapters.parsers.pdf import PDFParser
    from api.adapters.parsers.txt import TXTParser

    return {
        ".pdf": PDFParser,
        ".txt": TXTParser,
        ".md": MarkdownParser,
        ".markdown": MarkdownParser,
        ".py": CodeParser,
        ".js": CodeParser,
        ".mjs": CodeParser,
        ".ts": CodeParser,
        ".tsx": CodeParser,
        ".go": CodeParser,
        ".rs": CodeParser,
        ".java": CodeParser,
        ".csv": CSVParser,
        ".json": JSONParser,
    }


# Built once at import time (all parsers are lightweight dataclass-like objects).
_REGISTRY: dict[str, Callable[[ChunkingConfig | None], ParserAdapter]] = _make_registry()

# ``.docx``/``.pptx`` have no lightweight adapter — they always route to docling.
SUPPORTED_EXTENSIONS: set[str] = set(_REGISTRY) | set(DOCLING_EXTENSIONS)


def _should_use_docling(ext: str, parsers: ParserConfig) -> bool:
    """Whether ``ext`` should be parsed by docling given the parser config."""
    if ext in DOCLING_EXTENSIONS:
        return True
    return ext == ".pdf" and parsers.pdf_backend == "docling"


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
        config: The app's chunking config — tunes window/row sizes; when omitted
            each parser falls back to its built-in defaults.
        parsers: The app's parser config — selects the PDF backend
            (``pymupdf``/``docling``) and docling options. Defaults route every
            extension to its original lightweight adapter.

    Raises:
        ValueError: When no parser is registered for ``file_extension``.
    """
    from api.models.config import ParserConfig

    ext = file_extension.lower()
    parser_cfg = parsers or ParserConfig()
    if _should_use_docling(ext, parser_cfg):
        return _build_docling_parser(ext, parser_cfg)
    factory = _REGISTRY.get(ext)
    if factory is None:
        raise ValueError(f"No parser registered for extension: {ext!r}")
    return factory(config)
