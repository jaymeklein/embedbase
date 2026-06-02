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

from api.settings import settings

_CHUNK = 1024 * 1024  # 1 MiB read window


class FileTooLargeError(HTTPException):
    def __init__(self, limit_bytes: int) -> None:
        super().__init__(
            413, f"File exceeds maximum size of {limit_bytes} bytes"
        )


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
    """
    limit = max_bytes if max_bytes is not None else settings.max_file_size_bytes
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    # Fast path: trust a present, oversized Content-Length and reject up front.
    declared = upload.size
    if declared is not None and declared > limit:
        raise FileTooLargeError(limit)

    bytes_written = 0
    try:
        with open(tmp, "wb") as fh:
            while True:
                chunk = await upload.read(_CHUNK)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > limit:
                    raise FileTooLargeError(limit)
                fh.write(chunk)
    except BaseException:
        # Abort + cleanup on any failure (size guard, disk error, disconnect).
        tmp.unlink(missing_ok=True)
        raise

    # Atomic publish — the worker only ever sees the complete file.
    os.replace(tmp, dest)
    return bytes_written
