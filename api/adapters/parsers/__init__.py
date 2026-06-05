"""Parser registry — maps a file extension to a configured parser adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from api.adapters.base import ParserAdapter

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig


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

SUPPORTED_EXTENSIONS: set[str] = set(_REGISTRY)


def get_parser(
    file_extension: str, config: ChunkingConfig | None = None
) -> ParserAdapter:
    """Resolve the parser adapter for a given file extension.

    ``config`` (the app's chunking config) tunes window/row sizes; when omitted
    each parser falls back to its built-in defaults.
    """
    ext = file_extension.lower()
    factory = _REGISTRY.get(ext)
    if factory is None:
        raise ValueError(f"No parser registered for extension: {ext!r}")
    return factory(config)
