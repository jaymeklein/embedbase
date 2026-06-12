"""Unit tests for the Delivery 4 docling parser path.

The heavy ``docling`` dependency is *not* required for these tests: the adapter
imports docling lazily (only inside ``_build_pipeline`` / the CUDA check), so the
registry routing, config, device validation, and chunk-mapping logic are all
exercised with fakes. One gated test validates the real docling import paths when
the library happens to be installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from api.adapters.parsers import SUPPORTED_EXTENSIONS, get_parser
from api.adapters.parsers.docling_adapter import DoclingParser
from api.adapters.parsers.pdf import PDFParser
from api.models.config import AppConfig, ParserConfig

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "docling"

# ── Config ────────────────────────────────────────────────────────────────────


def test_parser_config_defaults():
    cfg = ParserConfig()
    assert cfg.pdf_backend == "pymupdf"
    assert cfg.docling_ocr is False
    assert cfg.docling_ocr_engine == "easyocr"
    assert cfg.docling_tables is True
    assert cfg.docling_device == "cpu"
    assert cfg.docling_flash_attention is False
    assert cfg.docling_artifacts_path is None


def test_app_config_has_parsers_section():
    assert isinstance(AppConfig().parsers, ParserConfig)


# ── Registry routing ──────────────────────────────────────────────────────────


def test_pdf_defaults_to_pymupdf():
    assert isinstance(get_parser(".pdf"), PDFParser)
    assert isinstance(get_parser(".pdf", parsers=ParserConfig(pdf_backend="pymupdf")), PDFParser)


def test_pdf_backend_docling_routes_to_docling():
    parser = get_parser(".pdf", parsers=ParserConfig(pdf_backend="docling"))
    assert isinstance(parser, DoclingParser)


def test_docx_and_pptx_always_route_to_docling():
    assert isinstance(get_parser(".docx"), DoclingParser)
    assert isinstance(get_parser(".pptx"), DoclingParser)
    # Even when the PDF backend is pymupdf, office formats still use docling.
    assert isinstance(get_parser(".docx", parsers=ParserConfig(pdf_backend="pymupdf")), DoclingParser)


def test_supported_extensions_include_office_formats():
    assert {".docx", ".pptx"}.issubset(SUPPORTED_EXTENSIONS)


def test_docling_supported_extensions():
    assert DoclingParser().supported_extensions() == [".pdf", ".docx", ".pptx"]


# ── Device validation ─────────────────────────────────────────────────────────


def test_invalid_device_raises():
    with pytest.raises(ValueError, match="docling_device"):
        DoclingParser(device="gpu")


def test_cuda_without_gpu_raises_at_init(monkeypatch):
    import api.adapters.parsers.docling_adapter as mod

    monkeypatch.setattr(mod, "_cuda_is_available", lambda: False)
    with pytest.raises(ValueError, match="no CUDA device"):
        mod.DoclingParser(device="cuda")


def test_cuda_with_gpu_constructs(monkeypatch):
    import api.adapters.parsers.docling_adapter as mod

    monkeypatch.setattr(mod, "_cuda_is_available", lambda: True)
    parser = mod.DoclingParser(device="cuda")
    assert parser._device == "cuda"


def test_constructing_cpu_parser_does_not_import_docling():
    DoclingParser(device="cpu")
    assert "docling" not in sys.modules  # lazy — heavy import deferred to parse()


# ── Artifacts path (configurable docling models location) ─────────────────────


class _FakePipelineOptions:
    """Stands in for docling's ``PdfPipelineOptions`` (no heavy import)."""

    def __init__(self, **kwargs: object) -> None: ...


def test_get_parser_passes_artifacts_path_to_docling():
    cfg = ParserConfig(pdf_backend="docling", docling_artifacts_path="/models/docling")
    # Both the PDF path and office formats route to docling and must receive the path.
    pdf = get_parser(".pdf", parsers=cfg)
    docx = get_parser(".docx", parsers=cfg)

    assert isinstance(pdf, DoclingParser)
    assert isinstance(docx, DoclingParser)
    assert pdf._artifacts_path == "/models/docling"
    assert docx._artifacts_path == "/models/docling"


def test_pdf_pipeline_options_sets_artifacts_path_when_configured(monkeypatch):
    parser = DoclingParser(artifacts_path="/models/docling")
    monkeypatch.setattr(parser, "_accelerator_options", lambda: object())
    opts = parser._pdf_pipeline_options(_FakePipelineOptions)
    assert opts.artifacts_path == "/models/docling"


def test_pdf_pipeline_options_omits_artifacts_path_when_unset(monkeypatch):
    parser = DoclingParser()  # default: None
    monkeypatch.setattr(parser, "_accelerator_options", lambda: object())
    opts = parser._pdf_pipeline_options(_FakePipelineOptions)
    assert not hasattr(opts, "artifacts_path")


# ── parse() chunk mapping (fake converter + chunker) ──────────────────────────


class _FakeMeta:
    def __init__(self, headings: list[str], page_no: int | None) -> None:
        self.headings = headings
        self.origin = type("Origin", (), {"page_no": page_no})()


class _FakeChunk:
    def __init__(self, text: str, headings: list[str], page_no: int | None) -> None:
        self.text = text
        self.meta = _FakeMeta(headings, page_no)


class _FakeChunker:
    def chunk(self, document: object) -> list[_FakeChunk]:
        return [
            _FakeChunk("Table text", ["Results", "Table 3"], 5),
            _FakeChunk("Intro text", [], None),
        ]

    def contextualize(self, chunk: _FakeChunk) -> str:
        return f"CTX::{chunk.text}"


class _FakeResult:
    document = object()


class _FakeConverter:
    def convert(self, file_path: str) -> _FakeResult:
        return _FakeResult()


def test_parse_maps_docling_chunks_to_chunk_model():
    parser = DoclingParser()
    parser._converter = _FakeConverter()
    parser._chunker = _FakeChunker()

    chunks = parser.parse("/data/report.pdf", "doc_x")

    assert len(chunks) == 2
    first = chunks[0]
    assert first.text == "CTX::Table text"  # contextualize() output is embedded
    assert first.metadata.parser == "docling"
    assert first.metadata.filename == "report.pdf"
    assert first.metadata.document_id == "doc_x"
    assert first.metadata.chunk_index == 0
    assert first.metadata.heading_path == "Results > Table 3"
    assert first.metadata.page_number == 5

    second = chunks[1]
    assert second.metadata.chunk_index == 1
    assert second.metadata.heading_path is None
    assert second.metadata.page_number is None


# ── Real docling (skipped unless installed) ───────────────────────────────────


def test_docling_import_paths_are_valid():
    """When docling is installed, the symbols _build_pipeline uses must exist."""
    pytest.importorskip("docling")
    from docling.chunking import HybridChunker  # noqa: F401
    from docling.datamodel.base_models import InputFormat  # noqa: F401
    from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: F401
    from docling.document_converter import DocumentConverter  # noqa: F401


# ── Real docling end-to-end against committed binary fixtures ──────────────────
#
# These run only where docling is actually installed (it is not in CI, which
# builds with INSTALL_ML=false, nor on the default host env). Each gates itself
# with ``importorskip`` so the fake-based tests above always run. Fixtures are
# generated by ``tests/fixtures/docling/generate_fixtures.py``.


def test_scanned_pdf_ocr_on_yields_chunks_ocr_off_yields_none():
    """Image-only PDF: OCR on -> text chunks; OCR off -> nothing (PyMuPDF-skip parity)."""
    pytest.importorskip("docling")
    pdf = str(_FIXTURES / "scanned_2page.pdf")

    with_ocr = DoclingParser(ocr=True, ocr_engine="rapidocr").parse(pdf, "scan")
    without_ocr = DoclingParser(ocr=False).parse(pdf, "scan")

    assert len(with_ocr) > 0
    assert len(without_ocr) == 0


def test_pdf_with_table_emits_markdown_pipes():
    """A ruled table is recognised and serialised as a Markdown table (``|`` cells)."""
    pytest.importorskip("docling")
    chunks = DoclingParser(table_structure=True).parse(str(_FIXTURES / "table.pdf"), "tbl")
    assert any("|" in chunk.text for chunk in chunks)


def test_docx_three_headings_populate_heading_path():
    """Every chunk from a fully-headed .docx carries its heading breadcrumb."""
    pytest.importorskip("docling")
    chunks = DoclingParser().parse(str(_FIXTURES / "headings.docx"), "docx")
    assert len(chunks) > 0
    assert all(chunk.metadata.heading_path for chunk in chunks)


def test_pptx_two_slides_titles_appear_in_heading_path():
    """A 2-slide deck yields >=2 chunks and surfaces both slide titles as headings."""
    pytest.importorskip("docling")
    chunks = DoclingParser().parse(str(_FIXTURES / "slides.pptx"), "pptx")
    assert len(chunks) >= 2
    headings = " ".join(chunk.metadata.heading_path or "" for chunk in chunks)
    assert "Overview" in headings
    assert "Roadmap" in headings
