"""Docling-backed parser adapter (Delivery 4 — optional heavy path).

Adds OCR + table-structure extraction for PDFs and unlocks ``.docx`` / ``.pptx``
ingestion. It sits *alongside* the PyMuPDF :class:`~api.adapters.parsers.pdf.PDFParser`
and is selected via ``config.parsers`` — it never replaces the default PDF path.

``docling`` (and ``torch`` for the CUDA check) are imported lazily so this module
imports — and the parser registry routes ``.docx``/``.pptx`` — without the heavy
dependency installed. The conversion pipeline is built on the first
:meth:`parse` call.

Pipeline construction is written against docling's documented API.
# verified against docling==2.102.0 / docling-core==2.82.0 (exercised locally):
# DocumentConverter, HybridChunker, PdfPipelineOptions, AcceleratorOptions,
# ChunkingSerializerProvider + MarkdownTableSerializer. Heavy optional path, so
# docling is not pinned in api/requirements.txt.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.models.chunk import Chunk, ChunkMetadata

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".pptx"]
_VALID_DEVICES = ("cpu", "cuda", "auto")
# Flash Attention 2 requires Ampere or newer; Turing (RTX 20 series, incl. the
# 2060 Super at 7.5) is below this and falls back to standard CUDA attention.
_FLASH_ATTENTION_MIN_CAPABILITY = (8, 0)
# Batch sizes: small CPU default vs a GPU-saturating batch when CUDA is detected.
_CPU_BATCH_SIZE = 8
_GPU_BATCH_SIZE = 64


def _cuda_is_available() -> bool:
    """Return whether torch sees a CUDA device (imports torch lazily)."""
    import torch

    return bool(torch.cuda.is_available())


def _cuda_compute_capability() -> tuple[int, int] | None:
    """Return the active CUDA device's (major, minor) capability, or None.

    Returns ``None`` when no CUDA device is visible. Imports torch lazily so the
    module stays importable without the heavy dependency installed.
    """
    import torch

    if not torch.cuda.is_available():
        return None
    return torch.cuda.get_device_capability()


def _flash_attention_installed() -> bool:
    """Return whether the ``flash_attn`` package is importable (no import side effect)."""
    return importlib.util.find_spec("flash_attn") is not None


@dataclass(frozen=True)
class AcceleratorProfile:
    """Resolved accelerator settings chosen for the host hardware."""

    device: str
    flash_attention: bool
    batch_size: int


_CPU_PROFILE = AcceleratorProfile(device="cpu", flash_attention=False, batch_size=_CPU_BATCH_SIZE)


def detect_accelerator() -> AcceleratorProfile:
    """Detect the best docling accelerator settings for the current host.

    Picks CUDA whenever a GPU is visible to torch, and enables Flash Attention 2
    only on Ampere+ GPUs (compute capability >= 8.0) that also have ``flash-attn``
    installed — so a Turing card such as the RTX 2060 Super (7.5) auto-selects
    CUDA without flash. Falls back to CPU when torch is missing or no GPU is
    present, so it is always safe to call (no configuration required).

    Returns:
        The resolved :class:`AcceleratorProfile`.
    """
    try:
        capability = _cuda_compute_capability()
    except ImportError:
        return _CPU_PROFILE
    if capability is None:
        return _CPU_PROFILE
    flash = capability >= _FLASH_ATTENTION_MIN_CAPABILITY and _flash_attention_installed()
    logger.info(
        "docling accelerator auto-detected: device=cuda capability=%d.%d flash_attention=%s",
        capability[0],
        capability[1],
        flash,
    )
    return AcceleratorProfile(device="cuda", flash_attention=flash, batch_size=_GPU_BATCH_SIZE)


def _validate_accelerator(device: str, flash_attention: bool) -> None:
    """Validate the device string and flash-attention support, failing fast.

    Args:
        device: Requested accelerator device (``cpu``/``cuda``/``auto``).
        flash_attention: Whether Flash Attention 2 was requested.

    Raises:
        ValueError: For an invalid ``device``; ``device="cuda"`` with no CUDA
            device; or ``flash_attention`` on a GPU below Ampere (compute
            capability < 8.0), such as the Turing RTX 2060 Super.
    """
    if device not in _VALID_DEVICES:
        raise ValueError(f"docling_device must be one of {_VALID_DEVICES}, got {device!r}")
    if device == "cuda" and not _cuda_is_available():
        raise ValueError("docling_device='cuda' but no CUDA device is available")
    if not flash_attention:
        return
    capability = _cuda_compute_capability()
    if capability is None:
        raise ValueError("docling_flash_attention=True but no CUDA device is available")
    if capability < _FLASH_ATTENTION_MIN_CAPABILITY:
        major, minor = capability
        raise ValueError(
            "docling_flash_attention=True requires an Ampere or newer GPU "
            f"(compute capability >= 8.0); found {major}.{minor}. Turing cards such "
            "as the RTX 2060 Super (7.5) are unsupported — use standard CUDA attention."
        )


class DoclingParser:
    """Parse PDF/DOCX/PPTX via docling into heading-aware embedding chunks."""

    def __init__(
        self,
        *,
        ocr: bool = False,
        ocr_engine: str = "easyocr",
        table_structure: bool = True,
        device: str = "auto",
        flash_attention: bool = False,
        ocr_batch_size: int = _CPU_BATCH_SIZE,
        layout_batch_size: int = _CPU_BATCH_SIZE,
        artifacts_path: str | None = None,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_tokens: int = 512,
    ) -> None:
        """Configure the parser (no heavy work yet).

        Args:
            ocr: Enable OCR for scanned / image-only pages.
            ocr_engine: ``"easyocr"`` (default) | ``"tesseract"`` | ``"rapidocr"``.
            table_structure: Recognise table structure (emitted as Markdown).
            device: ``"auto"`` (default — detect the GPU and configure flash
                attention + batch sizes automatically) | ``"cpu"`` | ``"cuda"``.
            flash_attention: Use Flash Attention 2 (RTX 30/40 + ``flash-attn``).
                Ignored when ``device="auto"`` (detection decides).
            ocr_batch_size: OCR batch size. Ignored when ``device="auto"``.
            layout_batch_size: Layout-model batch size. Ignored when ``device="auto"``.
            artifacts_path: Local directory holding the docling models; when set,
                docling loads from here instead of the default HuggingFace cache
                (offline / pinned models). ``None`` keeps docling's default.
            model_name: HF tokenizer used to size chunks to the embedder budget.
            max_tokens: Maximum tokens per chunk.

        Raises:
            ValueError: For an invalid ``device``; ``device="cuda"`` with no CUDA
                device; or ``flash_attention`` on a GPU below Ampere (fails fast
                at construction).
        """
        if device == "auto":
            # Detection yields a hardware-consistent profile, so no validation.
            profile = detect_accelerator()
            device = profile.device
            flash_attention = profile.flash_attention
            ocr_batch_size = layout_batch_size = profile.batch_size
        else:
            _validate_accelerator(device, flash_attention)
        self._ocr = ocr
        self._ocr_engine = ocr_engine
        self._table_structure = table_structure
        self._device = device
        self._flash_attention = flash_attention
        self._ocr_batch_size = ocr_batch_size
        self._layout_batch_size = layout_batch_size
        self._artifacts_path = artifacts_path
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
        if self._artifacts_path:
            opts.artifacts_path = self._artifacts_path
        return opts

    def _chunk_serializer_provider(self) -> Any:
        """Build a chunk serializer that renders tables as Markdown, not triplets.

        docling's ``HybridChunker`` defaults to a *triplet* table serializer
        (``"col = value"`` prose). The docling path is selected precisely when
        table structure matters, so the embedded chunk should keep the Markdown
        table (pipe-delimited) intact for retrieval.
        """
        from docling_core.transforms.chunker.hierarchical_chunker import (
            ChunkingDocSerializer,
            ChunkingSerializerProvider,
        )
        from docling_core.transforms.serializer.markdown import MarkdownTableSerializer

        class _MarkdownTableProvider(ChunkingSerializerProvider):
            def get_serializer(self, doc: Any) -> Any:
                return ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())

        return _MarkdownTableProvider()

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
        self._chunker = HybridChunker(
            tokenizer=tokenizer, merge_peers=True, serializer_provider=self._chunk_serializer_provider()
        )

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
