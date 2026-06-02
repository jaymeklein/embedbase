"""PDF parser — one chunk per page via PyMuPDF (fitz)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from api.models.chunk import Chunk, ChunkMetadata

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig


class PDFParser:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config

    def supported_extensions(self) -> list[str]:
        return [".pdf"]

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        import fitz  # PyMuPDF

        filename = os.path.basename(file_path)
        chunks: list[Chunk] = []
        with fitz.open(file_path) as doc:
            total_pages = doc.page_count
            chunk_index = 0
            for page_number, page in enumerate(doc, start=1):
                text = page.get_text("text").strip()
                if not text:
                    # Skip image-only / blank pages — nothing to embed.
                    continue
                chunks.append(
                    Chunk(
                        text=text,
                        metadata=ChunkMetadata(
                            source_file=file_path,
                            filename=filename,
                            parser="pdf",
                            document_id=document_id,
                            chunk_index=chunk_index,
                            page_number=page_number,
                            total_pages=total_pages,
                            char_count=len(text),
                        ),
                    )
                )
                chunk_index += 1
        return chunks
