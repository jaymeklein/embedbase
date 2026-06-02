"""JSON parser.

Two shapes are handled:

* **array of objects** → treated like CSV: each element serialized as
  ``key=val; key=val``, grouped N rows per chunk.
* **anything else** (nested object/array) → flattened to ``dot.notation=value``
  lines, packed into windows.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from api.models.chunk import Chunk, ChunkMetadata
from api.services.ingestion import count_tokens, row_based, sliding_window

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts/lists into ``dot.notation`` → scalar pairs."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(flatten(value, child))
    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            child = f"{prefix}[{idx}]"
            out.update(flatten(value, child))
    else:
        out[prefix] = obj
    return out


def _depth(obj: Any, level: int = 1) -> int:
    if isinstance(obj, dict) and obj:
        return max(_depth(v, level + 1) for v in obj.values())
    if isinstance(obj, list) and obj:
        return max(_depth(v, level + 1) for v in obj)
    return level


class JSONParser:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        if config is not None:
            self._rows_per_chunk = config.csv.rows_per_chunk
            self._max_tokens = config.sliding_window.max_tokens
            self._overlap = config.sliding_window.overlap_tokens
        else:
            self._rows_per_chunk = 10
            self._max_tokens = 512
            self._overlap = 64

    def supported_extensions(self) -> list[str]:
        return [".json"]

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)

        filename = os.path.basename(file_path)
        if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
            return self._parse_array(data, file_path, filename, document_id)
        return self._parse_nested(data, file_path, filename, document_id)

    def _parse_array(
        self, data: list[dict[str, Any]], file_path: str, filename: str, document_id: str
    ) -> list[Chunk]:
        rows = [
            "; ".join(f"{k}={v}" for k, v in flatten(record).items()) for record in data
        ]
        total_rows = len(rows)
        chunks: list[Chunk] = []
        offset = 0
        for chunk_index, batch in enumerate(
            row_based(rows, rows_per_chunk=self._rows_per_chunk)
        ):
            start, end = offset, offset + len(batch) - 1
            offset += len(batch)
            text = "\n".join(batch)
            chunks.append(
                Chunk(
                    text=text,
                    metadata=ChunkMetadata(
                        source_file=file_path,
                        filename=filename,
                        parser="json",
                        document_id=document_id,
                        chunk_index=chunk_index,
                        json_shape="array",
                        row_range=f"{start}-{end}",
                        total_rows=total_rows,
                        char_count=len(text),
                    ),
                )
            )
        return chunks

    def _parse_nested(
        self, data: Any, file_path: str, filename: str, document_id: str
    ) -> list[Chunk]:
        flat = flatten(data)
        lines = "\n".join(f"{k}={v}" for k, v in flat.items())
        pieces = (
            [lines]
            if count_tokens(lines) <= self._max_tokens
            else sliding_window(
                lines, max_tokens=self._max_tokens, overlap_tokens=self._overlap
            )
        )
        return [
            Chunk(
                text=piece,
                metadata=ChunkMetadata(
                    source_file=file_path,
                    filename=filename,
                    parser="json",
                    document_id=document_id,
                    chunk_index=i,
                    json_shape="object",
                    depth=_depth(data),
                    key_count=len(flat),
                    char_count=len(piece),
                ),
            )
            for i, piece in enumerate(pieces)
        ]
