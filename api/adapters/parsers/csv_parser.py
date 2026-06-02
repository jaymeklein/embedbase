"""CSV parser — N rows per chunk, ``col=val; col=val`` serialization."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from api.models.chunk import Chunk, ChunkMetadata
from api.services.ingestion import row_based

if TYPE_CHECKING:
    from api.models.config import ChunkingConfig


def serialize_row(columns: list[str], values: dict[str, object]) -> str:
    """Render one record as ``col=val; col=val`` for readable embeddings."""
    return "; ".join(f"{c}={values.get(c, '')}" for c in columns)


class CSVParser:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._rows_per_chunk = config.csv.rows_per_chunk if config else 10

    def supported_extensions(self) -> list[str]:
        return [".csv"]

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        import pandas as pd

        df = pd.read_csv(file_path, dtype=str, keep_default_na=False)
        columns = [str(c) for c in df.columns]
        total_rows = int(len(df))
        rows = [serialize_row(columns, row) for row in df.to_dict(orient="records")]

        filename = os.path.basename(file_path)
        chunks: list[Chunk] = []
        offset = 0
        for chunk_index, batch in enumerate(
            row_based(rows, rows_per_chunk=self._rows_per_chunk)
        ):
            start = offset
            end = offset + len(batch) - 1
            offset += len(batch)
            text = "\n".join(batch)
            chunks.append(
                Chunk(
                    text=text,
                    metadata=ChunkMetadata(
                        source_file=file_path,
                        filename=filename,
                        parser="csv",
                        document_id=document_id,
                        chunk_index=chunk_index,
                        columns=columns,
                        row_range=f"{start}-{end}",
                        total_rows=total_rows,
                        char_count=len(text),
                    ),
                )
            )
        return chunks
