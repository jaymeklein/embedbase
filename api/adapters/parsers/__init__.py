"""Parser registry — maps a file extension to a configured parser adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.adapters.base import ParserAdapter

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig


def get_parser(
    file_extension: str, config: ChunkingConfig | None = None
) -> ParserAdapter:
    """Resolve the parser adapter for a given file extension.

    ``config`` (the app's chunking config) tunes window/row sizes; when omitted
    each parser falls back to its built-in defaults.
    """
    ext = file_extension.lower()

    if ext == ".pdf":
        from api.adapters.parsers.pdf import PDFParser

        return PDFParser(config)

    if ext in (".txt",):
        from api.adapters.parsers.txt import TXTParser

        return TXTParser(config)

    if ext in (".md", ".markdown"):
        from api.adapters.parsers.markdown import MarkdownParser

        return MarkdownParser(config)

    if ext in (".py", ".js", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java"):
        from api.adapters.parsers.code import CodeParser

        return CodeParser(config)

    if ext == ".csv":
        from api.adapters.parsers.csv_parser import CSVParser

        return CSVParser(config)

    if ext == ".json":
        from api.adapters.parsers.json_parser import JSONParser

        return JSONParser(config)

    raise ValueError(f"No parser registered for extension: {ext!r}")


SUPPORTED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".markdown",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java",
    ".csv", ".json",
}
