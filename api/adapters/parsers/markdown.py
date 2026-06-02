"""Markdown parser — heading-boundary sections via mistune v3 AST."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from api.models.chunk import Chunk, ChunkMetadata
from api.services.ingestion import count_tokens, sliding_window

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig


def _inline_text(children: list[dict[str, Any]] | None) -> str:
    """Flatten inline tokens (text, emphasis, code, links, ...) to plain text."""
    if not children:
        return ""
    parts: list[str] = []
    for tok in children:
        if "raw" in tok:
            parts.append(tok["raw"])
        elif "children" in tok:
            parts.append(_inline_text(tok["children"]))
    return "".join(parts)


def _block_text(token: dict[str, Any]) -> str:
    """Best-effort plain-text rendering of a single block token."""
    ttype = token.get("type")
    if ttype == "blank_line":
        return ""
    if "raw" in token and "children" not in token:
        return token["raw"]
    if "children" in token:
        # list / blockquote nest block tokens; paragraph nests inline tokens.
        children = token["children"]
        if children and children[0].get("type") in {
            "paragraph", "list", "list_item", "block_text", "heading",
        }:
            return "\n".join(_block_text(c) for c in children)
        return _inline_text(children)
    return ""


class MarkdownParser:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        if config is not None:
            self._max_tokens = config.sliding_window.max_tokens
            self._overlap = config.sliding_window.overlap_tokens
        else:
            self._max_tokens = 512
            self._overlap = 64

    def supported_extensions(self) -> list[str]:
        return [".md", ".markdown"]

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        import mistune

        with open(file_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        md = mistune.create_markdown(renderer=None)
        tokens = md(text)

        sections = self._split_sections(tokens)
        filename = os.path.basename(file_path)

        chunks: list[Chunk] = []
        chunk_index = 0
        for heading_path, heading_level, body in sections:
            body = body.strip()
            if not body:
                continue
            pieces = (
                [body]
                if count_tokens(body) <= self._max_tokens
                else sliding_window(
                    body, max_tokens=self._max_tokens, overlap_tokens=self._overlap
                )
            )
            for piece in pieces:
                chunks.append(
                    Chunk(
                        text=piece,
                        metadata=ChunkMetadata(
                            source_file=file_path,
                            filename=filename,
                            parser="markdown",
                            document_id=document_id,
                            chunk_index=chunk_index,
                            heading_path=heading_path,
                            heading_level=heading_level,
                            char_count=len(piece),
                        ),
                    )
                )
                chunk_index += 1
        return chunks

    def _split_sections(
        self, tokens: list[dict[str, Any]]
    ) -> list[tuple[str | None, int | None, str]]:
        """Group tokens into (heading_path, heading_level, body) sections."""
        sections: list[tuple[str | None, int | None, str]] = []
        stack: list[str] = []  # heading titles indexed by depth
        cur_path: str | None = None
        cur_level: int | None = None
        cur_body: list[str] = []

        def flush() -> None:
            if cur_body:
                sections.append((cur_path, cur_level, "\n\n".join(cur_body)))

        for tok in tokens:
            if tok.get("type") == "heading":
                flush()
                level = int(tok.get("attrs", {}).get("level", 1))
                title = _inline_text(tok.get("children")).strip()
                stack[level - 1 :] = [title]
                cur_path = " > ".join(stack[:level])
                cur_level = level
                cur_body = [title]
            else:
                rendered = _block_text(tok).strip()
                if rendered:
                    cur_body.append(rendered)
        flush()
        return sections
