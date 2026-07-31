"""Streaming file upload with a hard size cap.

Files are streamed to a ``.tmp`` sibling and only atomically renamed into place
once the full body has been written within the size limit. This guarantees the
worker never observes a partially-written or oversized file: it either sees the
complete file at its final path or nothing at all.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException, UploadFile

_CHUNK = 1024 * 1024  # 1 MiB read window
# Fallback cap used only when no explicit limit is passed (e.g. before the live
# AppConfig is available). Callers normally pass the editable config's value.
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
# How many leading bytes to sniff for content-type validation at confirm time — enough for a
# magic number plus a text/binary judgement, never the whole object.
HEAD_SNIFF_BYTES = 4096


class FileTooLargeError(HTTPException):
    def __init__(self, limit_bytes: int) -> None:
        super().__init__(
            413, f"File exceeds maximum size of {limit_bytes} bytes"
        )


class ContentTypeMismatchError(HTTPException):
    def __init__(self, ext: str) -> None:
        super().__init__(
            415,
            f"Uploaded bytes do not match the declared type {ext or '(none)'} — "
            "the file content is a different format.",
        )


class EmptyFileError(HTTPException):
    def __init__(self) -> None:
        # A file with no ingestible content parses to zero chunks, so it can never be
        # indexed — it would sit forever showing a no-op "Index" action. Reject at upload.
        super().__init__(422, "File is empty")


# Extension -> byte prefix(es) the object's content MUST start with. OOXML (.docx/.pptx/.xlsx) is
# a ZIP container. Extensions absent here have no known signature and are not content-checked.
_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".docx": (b"PK\x03\x04",),
    ".pptx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
}
# Extensions whose content is validated as text: no NUL byte and not a known binary signature.
_TEXT_EXTS = frozenset({".txt", ".md", ".markdown", ".csv"})
# A leading Unicode BOM declares a text encoding (UTF-8/16/32); UTF-16/32 text legitimately
# contains NUL bytes, so a BOM short-circuits the NUL check below.
_TEXT_BOMS: tuple[bytes, ...] = (
    b"\xef\xbb\xbf",  # UTF-8
    b"\xff\xfe",  # UTF-16 LE (also the prefix of the UTF-32 LE BOM)
    b"\xfe\xff",  # UTF-16 BE
    b"\x00\x00\xfe\xff",  # UTF-32 BE — leading NULs, not covered by the 2-byte entries
)
# Binary signatures that must NOT head a text file (catches e.g. a PNG renamed to .md).
_BINARY_MAGICS: tuple[bytes, ...] = (
    b"%PDF-", b"PK\x03\x04", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff",
    b"GIF87a", b"GIF89a", b"\x1f\x8b", b"BM", b"\x00\x00\x01\x00",
    b"II*\x00", b"MM\x00*", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07",
)


def validate_content(ext: str, head: bytes) -> None:
    """Raise :class:`ContentTypeMismatchError` (415) if ``head`` isn't the declared ``ext``.

    Validates the types we have signatures for — PDF, OOXML/ZIP, and text — from the object's
    leading bytes. Extensions without a known signature pass (we can't sniff an arbitrary type);
    the guarantee is that a *known* type can't be substituted (e.g. a PNG uploaded as a .pdf).
    """
    ext = ext.lower()  # defensive: this gates a security check — don't depend on caller casing
    signatures = _MAGIC_SIGNATURES.get(ext)
    if signatures is not None:
        if head.startswith(signatures):
            return
        # A PDF may carry a few junk bytes before %PDF- (readers tolerate it in the first 1 KB).
        if ext == ".pdf" and b"%PDF-" in head[:1024]:
            return
        raise ContentTypeMismatchError(ext)
    if ext in _TEXT_EXTS:
        if head.startswith(_TEXT_BOMS):
            return  # a Unicode BOM declares a text encoding — allow, NUL bytes and all
        if b"\x00" in head or head.startswith(_BINARY_MAGICS):
            raise ContentTypeMismatchError(ext)


def resolve_max_bytes(max_bytes: int | None) -> int:
    """The effective upload size cap — the caller's configured limit, or the fallback default.

    Shared so the streaming guard (:func:`stream_upload_with_size_guard`) and the presigned-upload
    confirm check (``documents.confirm_upload``) apply the *same* limit — the presigned PUT lands
    bytes in storage directly, bypassing the stream guard, so confirm re-checks the size here.
    """
    return max_bytes if max_bytes is not None else _DEFAULT_MAX_BYTES


async def stream_upload_with_size_guard(
    upload: UploadFile,
    dest_path: str | Path,
    *,
    max_bytes: int | None = None,
) -> int:
    """Stream ``upload`` to ``dest_path``, aborting if it exceeds ``max_bytes``.

    Returns the number of bytes written. Uses the ``Content-Length`` header as a
    fast-path rejection before reading any body, then re-checks the running total
    while streaming (the header is advisory and may be absent or wrong).

    Also rejects a file with no ingestible content — zero bytes *or* only whitespace
    (e.g. a lone ``\\r\\n``) — since it parses to zero chunks and could never be
    indexed. Both aborts clean up the ``.tmp`` so nothing is published.
    """
    limit = resolve_max_bytes(max_bytes)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    # Fast path: trust a present, oversized Content-Length and reject up front.
    declared = upload.size
    if declared is not None and declared > limit:
        raise FileTooLargeError(limit)

    bytes_written = 0
    saw_content = False
    try:
        with open(tmp, "wb") as fh:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > limit:
                    raise FileTooLargeError(limit)
                # Any non-whitespace byte makes the file ingestible. The flag
                # short-circuits the strip() once content is seen, so a large binary
                # is trimmed at most once (its first bytes are non-whitespace anyway).
                if not saw_content and chunk.strip():
                    saw_content = True
                fh.write(chunk)
        if not saw_content:
            raise EmptyFileError()
    except BaseException:
        # Abort + cleanup on any failure (size guard, disk error, disconnect).
        tmp.unlink(missing_ok=True)
        raise

    # Atomic publish — the worker only ever sees the complete file.
    os.replace(tmp, dest)
    return bytes_written
