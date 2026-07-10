"""Shared query-filter infrastructure for the paginated listing endpoints.

The documents listing (:mod:`api.services.documents`) and the ingestion-job listing
(:mod:`api.services.jobs`) build their WHERE clause the same way, so the shared pieces live here:
the :class:`FilterSpec` base and the :func:`to_conditions` combiner that both assemble their
per-filter specs with (the specs themselves live in :mod:`api.services.document_filters` and
:mod:`api.services.job_filters`), plus two SQL idioms — an inclusive date-only upper bound
(:func:`inclusive_end`) and a LIKE-escaped case-insensitive substring (:func:`ilike_contains`).
The timestamp columns store ``isoformat()`` strings, so lexical comparison is chronological.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


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


class FilterSpec:
    """One optional filter on a paginated listing.

    Holds a single query value and turns it into a SQLAlchemy boolean condition via
    :meth:`to_condition`, which returns ``None`` when the value is unset (so the caller skips it).
    ``to_condition`` is ``async`` because some filters resolve their condition with a DB lookup
    (e.g. the documents tag filter); value-only filters ignore ``db``. Subclass one per filter and
    assemble the active conditions with :func:`to_conditions` — see the per-listing spec modules
    :mod:`api.services.document_filters` and :mod:`api.services.job_filters`.
    """

    def __init__(self, value: Any) -> None:
        self.value = value

    async def to_condition(self, db: AsyncSession) -> Any | None:
        raise NotImplementedError


async def to_conditions(specs: Iterable[FilterSpec], db: AsyncSession) -> list[Any]:
    """Resolve each spec to its condition, dropping the ones whose value was unset (``None``)."""
    conds: list[Any] = []
    for spec in specs:
        cond = await spec.to_condition(db)
        if cond is not None:
            conds.append(cond)
    return conds
