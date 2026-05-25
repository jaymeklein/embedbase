from api.adapters.base import ParserAdapter


def get_parser(file_extension: str) -> ParserAdapter:
    """Resolve the parser adapter for a given file extension."""
    ext = file_extension.lower()

    if ext == ".pdf":
        from api.adapters.parsers.pdf import PDFParser
        return PDFParser()

    if ext in (".txt",):
        from api.adapters.parsers.txt import TXTParser
        return TXTParser()

    if ext in (".md", ".markdown"):
        from api.adapters.parsers.markdown import MarkdownParser
        return MarkdownParser()

    if ext in (".py", ".js", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java"):
        from api.adapters.parsers.code import CodeParser
        return CodeParser()

    if ext == ".csv":
        from api.adapters.parsers.csv_parser import CSVParser
        return CSVParser()

    if ext == ".json":
        from api.adapters.parsers.json_parser import JSONParser
        return JSONParser()

    raise ValueError(f"No parser registered for extension: {ext!r}")


SUPPORTED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".markdown",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java",
    ".csv", ".json",
}
