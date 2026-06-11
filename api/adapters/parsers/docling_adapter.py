"""Docling-backed parser adapter (Delivery 4 — optional heavy path).

Adds OCR + table-structure extraction for PDFs and unlocks ``.docx`` / ``.pptx``
ingestion. It sits *alongside* the PyMuPDF :class:`~api.adapters.parsers.pdf.PDFParser`
and is selected via ``config.parsers`` — it never replaces the default PDF path.

``docling`` (and ``torch`` for the CUDA check) are imported lazily so this module
imports — and the parser registry routes ``.docx``/``.pptx`` — without the heavy
dependency installed. The conversion pipeline is built on the first
:meth:`parse` call.

Pipeline construction is written against docling's documented API.
# verified against docling docs (docling>=2.0): DocumentConverter, HybridChunker,
# PdfPipelineOptions, AcceleratorOptions — not pinned in api/requirements.txt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from api.models.chunk import Chunk, ChunkMetadata

SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".pptx"]
_VALID_DEVICES = ("cpu", "cuda", "auto")


def _cuda_is_available() -> bool:
    """Return whether torch sees a CUDA device (imports torch lazily)."""
    import torch

    return bool(torch.cuda.is_available())


class DoclingParser:
    """Parse PDF/DOCX/PPTX via docling into heading-aware embedding chunks."""

    def __init__(
        self,
        *,
        ocr: bool = False,
        ocr_engine: str = "easyocr",
        table_structure: bool = True,
        device: str = "cpu",
        flash_attention: bool = False,
        ocr_batch_size: int = 8,
        layout_batch_size: int = 8,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_tokens: int = 512,
    ) -> None:
        """Configure the parser (no heavy work yet).

        Args:
            ocr: Enable OCR for scanned / image-only pages.
            ocr_engine: ``"easyocr"`` (default) | ``"tesseract"`` | ``"rapidocr"``.
            table_structure: Recognise table structure (emitted as Markdown).
            device: ``"cpu"`` (default) | ``"cuda"`` | ``"auto"``.
            flash_attention: Use Flash Attention 2 (RTX 30/40 + ``flash-attn``).
            ocr_batch_size: OCR batch size (bump to ~64 on GPU).
            layout_batch_size: Layout-model batch size (bump to ~64 on GPU).
            model_name: HF tokenizer used to size chunks to the embedder budget.
            max_tokens: Maximum tokens per chunk.

        Raises:
            ValueError: For an invalid ``device``, or ``device="cuda"`` when no
                CUDA device is available (fails fast at construction).
        """
        if device not in _VALID_DEVICES:
            raise ValueError(f"docling_device must be one of {_VALID_DEVICES}, got {device!r}")
        if device == "cuda" and not _cuda_is_available():
            raise ValueError("docling_device='cuda' but no CUDA device is available")
        self._ocr = ocr
        self._ocr_engine = ocr_engine
        self._table_structure = table_structure
        self._device = device
        self._flash_attention = flash_attention
        self._ocr_batch_size = ocr_batch_size
        self._layout_batch_size = layout_batch_size
        self._model_name = model_name
        self._max_tokens = max_tokens
        self._converter: Any = None
        self._chunker: Any = None

    def supported_extensions(self) -> list[str]:
        """Return the file extensions this parser can handle."""
        return list(SUPPORTED_EXTENSIONS)

    def _accelerator_options(self) -> Any:
        """Build docling ``AcceleratorOptions`` from the configured device."""
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions

        device_map = {
            "cpu": AcceleratorDevice.CPU,
            "cuda": AcceleratorDevice.CUDA,
            "auto": AcceleratorDevice.AUTO,
        }
        return AcceleratorOptions(
            device=device_map[self._device],
            cuda_use_flash_attention2=self._flash_attention,
        )

    def _ocr_options(self) -> Any:
        """Build the OCR engine options for the non-default engines."""
        from docling.datamodel.pipeline_options import RapidOcrOptions, TesseractOcrOptions

        if self._ocr_engine == "tesseract":
            return TesseractOcrOptions()
        return RapidOcrOptions()

    def _pdf_pipeline_options(self, options_cls: Any) -> Any:
        """Assemble the PDF pipeline options (OCR, tables, device, batch sizes)."""
        opts = options_cls(do_ocr=self._ocr, do_table_structure=self._table_structure)
        opts.accelerator_options = self._accelerator_options()
        for attr, value in (
            ("ocr_batch_size", self._ocr_batch_size),
            ("layout_batch_size", self._layout_batch_size),
        ):
            if hasattr(opts, attr):
                setattr(opts, attr, value)
        if self._ocr and self._ocr_engine != "easyocr":
            opts.ocr_options = self._ocr_options()
        return opts

    def _build_pipeline(self) -> None:
        """Construct the docling converter + chunker (lazy, heavy import)."""
        from docling.chunking import HybridChunker
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import (
            DocumentConverter,
            PdfFormatOption,
            WordFormatOption,
        )
        from docling.pipeline.simple_pipeline import SimplePipeline
        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX],
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=self._pdf_pipeline_options(PdfPipelineOptions)
                ),
                InputFormat.DOCX: WordFormatOption(pipeline_cls=SimplePipeline),
            },
        )
        tokenizer = HuggingFaceTokenizer.from_pretrained(
            model_name=self._model_name, max_tokens=self._max_tokens
        )
        self._chunker = HybridChunker(tokenizer=tokenizer, merge_peers=True)

    def parse(self, file_path: str, document_id: str) -> list[Chunk]:
        """Convert ``file_path`` to a list of heading-aware :class:`Chunk` objects.

        Args:
            file_path: Path to the PDF/DOCX/PPTX on disk.
            document_id: Owning document id (used for deterministic chunk ids).

        Returns:
            One chunk per docling ``HybridChunker`` chunk; the embedded text is the
            ``contextualize``-d form (heading breadcrumb prepended).
        """
        if self._converter is None or self._chunker is None:
            self._build_pipeline()
        result = self._converter.convert(file_path)
        return [
            self._to_chunk(chunk, idx, file_path, document_id)
            for idx, chunk in enumerate(self._chunker.chunk(result.document))
        ]

    def _to_chunk(self, chunk: Any, idx: int, file_path: str, document_id: str) -> Chunk:
        """Map a single docling chunk onto the EmbedBase :class:`Chunk` model."""
        embed_text = self._chunker.contextualize(chunk)
        meta = getattr(chunk, "meta", None)
        page_number = getattr(getattr(meta, "origin", None), "page_no", None)
        headings = getattr(meta, "headings", None)
        heading_path = " > ".join(headings) if headings else None
        return Chunk(
            text=embed_text,
            metadata=ChunkMetadata(
                source_file=file_path,
                filename=Path(file_path).name,
                parser="docling",
                document_id=document_id,
                chunk_index=idx,
                page_number=page_number,
                heading_path=heading_path,
            ),
        )
