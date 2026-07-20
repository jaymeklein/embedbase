"""Composable filter specifications for the ingestion-queue (jobs) listing.

The ingestion-queue sibling of :mod:`api.services.document_filters`: each optional filter on
:func:`api.services.jobs.list_jobs` is one :class:`~api.services.filters.FilterSpec`, so a new
filter is a new subclass plus one line in :func:`build_specs`, never another ``if`` in the query
body. Both listings share the ``FilterSpec`` base and the :func:`~api.services.filters.to_conditions`
combiner, so they extend the same way — the specs differ only in which table/column each filters.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import job_records as job_t
from api.models.document import JobListQuery
from api.services.filters import FilterSpec, ilike_contains, inclusive_end


class StatusSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return job_t.c.status == self.value if self.value else None


class FilenameSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return ilike_contains(job_t.c.filename, self.value) if self.value else None


class FileTypeSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return job_t.c.file_type == self.value if self.value else None


class CollectionSpec(FilterSpec):
    """Case-insensitive substring on the (left-joined) collection name."""

    async def to_condition(self, db: AsyncSession) -> Any | None:
        return ilike_contains(col_t.c.name, self.value) if self.value else None


class CollectionIdSpec(FilterSpec):
    """Exact collection id, straight off the job row (no join, no fuzziness).

    The sibling of :class:`CollectionSpec` for callers that must scope to exactly one
    collection — a bulk retry driven from the indexing page, say. The name substring
    cannot do that: "leis" also matches "leis-antigas".
    """

    async def to_condition(self, db: AsyncSession) -> Any | None:
        return job_t.c.collection_id == self.value if self.value else None


class CreatedAfterSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return job_t.c.created_at >= self.value if self.value else None


class CreatedBeforeSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        # inclusive_end expands a date-only bound to end-of-day so the bound day is covered.
        return job_t.c.created_at <= inclusive_end(self.value) if self.value else None


def build_specs(query: JobListQuery) -> list[FilterSpec]:
    """Every filter carried by ``query`` as a spec. Add a filter here, not as an ``if`` in list_jobs."""
    return [
        StatusSpec(query.status),
        FilenameSpec(query.filename),
        FileTypeSpec(query.file_type),
        CollectionSpec(query.collection),
        CollectionIdSpec(query.collection_id),
        CreatedAfterSpec(query.created_after),
        CreatedBeforeSpec(query.created_before),
    ]
