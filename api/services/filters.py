"""Shared query-filter helpers for the paginated listing endpoints.

Both the documents listing (:mod:`api.services.documents`) and the ingestion-job listing
(:mod:`api.services.jobs`) apply the same two idioms — an inclusive date-only upper bound and a
LIKE-escaped case-insensitive substring — so they live here once instead of being copied per
service. The timestamp columns store ``isoformat()`` strings, so lexical comparison is chronological.
"""

from __future__ import annotations

from typing import Any


def inclusive_end(bound: str) -> str:
    """Expand a date-only upper bound (``YYYY-MM-DD``) to end-of-day so ``<= bound`` includes that
    whole day's ISO timestamps; a full timestamp passes through unchanged. Centralising it here
    gives every caller (UI, MCP, scripts) inclusive date filtering without its own fix-up.
    """
    if len(bound) == 10 and bound[4] == "-" and bound[7] == "-":
        return f"{bound}T23:59:59.999999"
    return bound


def ilike_contains(column: Any, term: str) -> Any:
    """A case-insensitive substring match with LIKE metacharacters escaped, so a literal ``%`` or
    ``_`` in ``term`` is matched literally instead of as a wildcard. Returns the SQL expression to
    append to a WHERE clause.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")
