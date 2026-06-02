"""Code parser — one chunk per symbol (function/class/method) via tree-sitter."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from api.models.chunk import Chunk, ChunkMetadata
from api.services.ingestion import count_tokens

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig

# Node types that represent a top-level symbol worth a chunk, per language.
_SYMBOL_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition"},
    "javascript": {
        "function_declaration", "generator_function_declaration",
        "class_declaration", "method_definition",
    },
    "typescript": {
        "function_declaration", "class_declaration", "method_definition",
        "interface_declaration", "abstract_class_declaration",
    },
    "tsx": {
        "function_declaration", "class_declaration", "method_definition",
        "interface_declaration", "abstract_class_declaration",
    },
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {
        "function_item", "struct_item", "enum_item", "trait_item", "impl_item",
    },
    "java": {
        "method_declaration", "class_declaration", "interface_declaration",
        "constructor_declaration",
    },
}

_EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}


def _classify(node_type: str) -> str:
    t = node_type.lower()
    if "method" in t or "constructor" in t:
        return "method"
    if any(k in t for k in ("class", "struct", "enum", "trait", "interface", "impl", "type")):
        return "class"
    return "function"


def _load_language(lang: str) -> Any:
    from tree_sitter import Language

    if lang == "python":
        import tree_sitter_python as m
        return Language(m.language())
    if lang == "javascript":
        import tree_sitter_javascript as m
        return Language(m.language())
    if lang == "typescript":
        import tree_sitter_typescript as m
        return Language(m.language_typescript())
    if lang == "tsx":
        import tree_sitter_typescript as m
        return Language(m.language_tsx())
    if lang == "go":
        import tree_sitter_go as m
        return Language(m.language())
    if lang == "rust":
        import tree_sitter_rust as m
        return Language(m.language())
    if lang == "java":
        import tree_sitter_java as m
        return Language(m.language())
    raise ValueError(f"Unsupported code language: {lang!r}")


class CodeParser:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        if config is not None:
            self._max_symbol_tokens = config.code.max_symbol_tokens
            self._fallback_lines = config.code.fallback_window_lines
        else:
            self._max_symbol_tokens = 4096
            self._fallback_lines = 50

    def supported_extensions(self) -> list[str]:
        return list(_EXT_LANG)

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        from tree_sitter import Parser

        ext = os.path.splitext(file_path)[1].lower()
        lang_name = _EXT_LANG[ext]
        with open(file_path, "rb") as fh:
            source = fh.read()

        language = _load_language(lang_name)
        parser = Parser(language)
        tree = parser.parse(source)

        symbols = self._collect_symbols(tree.root_node, lang_name)
        filename = os.path.basename(file_path)

        chunks: list[Chunk] = []
        chunk_index = 0

        if not symbols:
            # No recognizable symbols — fall back to line-window chunks.
            for piece in self._line_windows(source.decode("utf-8", "replace")):
                chunks.append(
                    self._build(
                        piece, file_path, filename, document_id, chunk_index,
                        lang_name, None, None, None, None,
                    )
                )
                chunk_index += 1
            return chunks

        for node, name in symbols:
            text = source[node.start_byte : node.end_byte].decode("utf-8", "replace")
            sym_type = _classify(node.type)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1

            if count_tokens(text) <= self._max_symbol_tokens:
                pieces = [text]
            else:
                pieces = self._line_windows(text)

            for piece in pieces:
                chunks.append(
                    self._build(
                        piece, file_path, filename, document_id, chunk_index,
                        lang_name, name, sym_type, start_line, end_line,
                    )
                )
                chunk_index += 1
        return chunks

    def _collect_symbols(self, root: Any, lang: str) -> list[tuple[Any, str | None]]:
        wanted = _SYMBOL_TYPES[lang]
        found: list[tuple[Any, str | None]] = []

        def walk(node: Any) -> None:
            if node.type in wanted:
                name_node = node.child_by_field_name("name")
                name = (
                    name_node.text.decode("utf-8", "replace") if name_node else None
                )
                found.append((node, name))
            for child in node.children:
                walk(child)

        walk(root)
        return found

    def _line_windows(self, text: str) -> list[str]:
        lines = text.splitlines()
        step = max(self._fallback_lines, 1)
        out = [
            "\n".join(lines[i : i + step]).strip()
            for i in range(0, len(lines), step)
        ]
        return [w for w in out if w]

    def _build(
        self,
        text: str,
        file_path: str,
        filename: str,
        document_id: str,
        chunk_index: int,
        language: str,
        symbol_name: str | None,
        symbol_type: str | None,
        start_line: int | None,
        end_line: int | None,
    ) -> Chunk:
        return Chunk(
            text=text,
            metadata=ChunkMetadata(
                source_file=file_path,
                filename=filename,
                parser="code",
                document_id=document_id,
                chunk_index=chunk_index,
                language=language,
                symbol_name=symbol_name,
                symbol_type=symbol_type,
                start_line=start_line,
                end_line=end_line,
                char_count=len(text),
            ),
        )
