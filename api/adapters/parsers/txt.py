"""Plain-text parser — encoding detection, paragraph packing, sliding fallback."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from api.models.chunk import Chunk, ChunkMetadata
from api.services.ingestion import count_tokens, sliding_window

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig


class TXTParser:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        if config is not None:
            self._max_tokens = config.sliding_window.max_tokens
            self._overlap = config.sliding_window.overlap_tokens
        else:
            self._max_tokens = 512
            self._overlap = 64

    def supported_extensions(self) -> list[str]:
        return [".txt"]

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        import chardet

        with open(file_path, "rb") as fh:
            raw = fh.read()
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"
        text = raw.decode(encoding, errors="replace")

        segments = self._segment(text)
        filename = os.path.basename(file_path)
        return [
            Chunk(
                text=seg,
                metadata=ChunkMetadata(
                    source_file=file_path,
                    filename=filename,
                    parser="txt",
                    document_id=document_id,
                    chunk_index=i,
                    encoding=encoding,
                    char_count=len(seg),
                ),
            )
            for i, seg in enumerate(segments)
        ]

    def _segment(self, text: str) -> list[str]:
        """Pack paragraphs (``\\n\\n`` separated) into windows up to max_tokens.

        A single paragraph larger than the window is split with a sliding window.
        """
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        segments: list[str] = []
        buf: list[str] = []
        buf_tokens = 0

        def flush() -> None:
            nonlocal buf, buf_tokens
            if buf:
                segments.append("\n\n".join(buf))
                buf = []
                buf_tokens = 0

        for para in paragraphs:
            tokens = count_tokens(para)
            if tokens > self._max_tokens:
                flush()
                segments.extend(
                    sliding_window(
                        para,
                        max_tokens=self._max_tokens,
                        overlap_tokens=self._overlap,
                    )
                )
            elif buf_tokens + tokens > self._max_tokens:
                flush()
                buf = [para]
                buf_tokens = tokens
            else:
                buf.append(para)
                buf_tokens += tokens
        flush()
        return segments
