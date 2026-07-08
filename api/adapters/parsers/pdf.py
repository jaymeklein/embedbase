"""PDF parser — one chunk per page via PyMuPDF (fitz), with heading propagation.

These PDFs expose no document outline (``get_toc()`` is empty), so headings are
recovered heuristically from font size: a line whose largest span is about a point
larger than the document's body text is treated as a heading. A running hierarchy
(nested by the heading number's depth — ``7`` → ``7.9`` → ``7.9.1``) is carried
across pages so every page chunk — *including continuation pages that contain no
heading of their own* — is tagged with, and prefixed by, the section it belongs to.

That closes two gaps the plain per-page split left open:
  * a mid-section page was orphaned from its title (bad for retrieval — it can't
    match the section name — and for the context handed to an LLM), and
  * section titles never reached chunk metadata (``heading_path`` was always None).

Both the *multiple headings on one page* and *one section across several pages*
cases are handled: each page's headings update the stack in reading order, and a
page with none inherits the section still on the stack.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from itertools import islice
from typing import TYPE_CHECKING, Any

from api.models.chunk import Chunk, ChunkMetadata

if TYPE_CHECKING:
    from collections.abc import Callable

    from api.models.config import ChunkingConfig

# A heading line begins with a section number — "7", "7.9", "7.11.1"; the depth
# (dots + 1) is its level, so the running stack nests sub-sections under parents.
_HEADING_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\s+\S")
# Points a heading line's largest span must exceed the body's modal size by. ~1pt
# separates the 11pt headings from 10pt body here; kept below 1 to tolerate the
# rounding jitter (10.0/10.1) seen in the extracted spans.
_HEADING_SIZE_MARGIN = 0.9
# A heading is short; guards against a whole large-font paragraph (a pull quote,
# a cover blurb) being mistaken for one.
_HEADING_MAX_WORDS = 25
# Body font size is uniform across a document, so the modal (body) size is sampled from
# the first N pages instead of re-scanning every page — a large PDF can't afford a second
# full layout pass just to find it (headings are detected in the main per-page loop).
_MODAL_SAMPLE_PAGES = 50


class PDFParser:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self._config = config

    def parse(
        self,
        file_path: str,
        document_id: str,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[Chunk]:
        import fitz  # PyMuPDF

        filename = os.path.basename(file_path)
        chunks: list[Chunk] = []
        with fitz.open(file_path) as doc:
            total_pages = doc.page_count
            # Lay out the sample pages ONCE and reuse it for both the modal-body-size estimate
            # and heading detection, so those pages aren't extracted twice (get_text("dict") is
            # the costly call). Pages past the sample are laid out on demand in the loop.
            sample_dicts = [_page_layout(page) for page in islice(doc, _MODAL_SAMPLE_PAGES)]
            min_heading_size = _modal_body_size(sample_dicts) + _HEADING_SIZE_MARGIN
            stack: list[tuple[int, str]] = []  # (level, title) — the current hierarchy
            chunk_index = 0
            for page_number, page in enumerate(doc, start=1):
                if on_progress:
                    # Report every page scanned (incl. blank/skipped) for smooth progress.
                    on_progress(page_number, total_pages)
                page_dict = (
                    sample_dicts[page_number - 1]
                    if page_number <= len(sample_dicts)
                    else _page_layout(page)
                )
                # Fold this page's headings into the running hierarchy first, so the page
                # is tagged with the section its body sits under: the last heading on the
                # page, or — on a continuation page with none — the inherited section.
                for level, title in _page_headings(page_dict, min_heading_size):
                    while stack and stack[-1][0] >= level:
                        stack.pop()
                    stack.append((level, title))
                text = page.get_text("text").strip()
                if not text:
                    # Skip image-only / blank pages — nothing to embed.
                    continue
                heading_path = " > ".join(title for _, title in stack) or None
                # Prefix the breadcrumb so the embedding *and* the retrieved chunk carry
                # the section even when the page's own text contains no heading.
                body = f"{heading_path}\n\n{text}" if heading_path else text
                chunks.append(
                    Chunk(
                        text=body,
                        metadata=ChunkMetadata(
                            source_file=file_path,
                            filename=filename,
                            parser="pdf",
                            document_id=document_id,
                            chunk_index=chunk_index,
                            page_number=page_number,
                            total_pages=total_pages,
                            char_count=len(body),
                            heading_path=heading_path,
                            heading_level=stack[-1][0] if stack else None,
                        ),
                    )
                )
                chunk_index += 1
        return chunks


def _page_layout(page: Any) -> dict[str, Any]:
    """``get_text("dict")`` for a page, or ``{}`` if extraction fails (malformed page)."""
    try:
        return page.get_text("dict")
    except Exception:  # pragma: no cover - malformed page; degrade to no layout
        return {}


def _modal_body_size(page_dicts: list[dict[str, Any]]) -> float:
    """Most common span font size across the sampled pages' layout — the body size.

    Used as the baseline a heading must rise above. Takes pre-extracted ``get_text("dict")``
    results so the sampled pages are laid out once and shared with heading detection rather
    than extracted twice. Best-effort: no measurable spans falls back to 10.0 (a typical body
    size), so headings are detected relative to that default instead of the measured value.
    """
    counts: Counter[float] = Counter()
    for page_dict in page_dicts:
        for block in page_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size")
                    if size is not None and span.get("text", "").strip():
                        counts[round(size, 1)] += 1
    return counts.most_common(1)[0][0] if counts else 10.0


def _page_headings(page_dict: dict[str, Any], min_heading_size: float) -> list[tuple[int, str]]:
    """Heading ``(level, title)`` pairs on a page, in reading order.

    Takes a pre-extracted ``get_text("dict")`` layout. A heading line is one whose largest
    span reaches ``min_heading_size``. A wrapped heading — a heading-size line that does *not*
    begin with a new section number — is merged into the preceding heading's title, so a title
    split across two lines ("7.7 …do valor" + "do contrato") stays a single heading; the merge
    stops once the title reaches ``_HEADING_MAX_WORDS`` so a run of heading-size lines (stylised
    intros, caption blocks) can't accrete into one unbounded ``heading_path``.
    """
    headings: list[list[Any]] = []  # [level, title]
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans or max(round(s.get("size", 0.0), 1) for s in spans) < min_heading_size:
                continue
            text = " ".join(" ".join(s["text"] for s in spans).split())  # collapse tabs
            if not text or len(text.split()) > _HEADING_MAX_WORDS:
                continue  # empty, or too long to be a heading
            match = _HEADING_NUMBER.match(text)
            if match:
                headings.append([match.group(1).count(".") + 1, text])
            elif headings and len(headings[-1][1].split()) + len(text.split()) <= _HEADING_MAX_WORDS:
                headings[-1][1] += " " + text  # wrapped continuation, still heading-length
            # else: a heading-size line with no number and no prior heading on the page (stray
            # large text), or a continuation that would overflow the title — skip it.
    return [(level, title) for level, title in headings]
