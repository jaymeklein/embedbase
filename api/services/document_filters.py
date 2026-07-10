"""Composable filter specifications for the document listing.

Each optional filter on :func:`api.services.documents.list_documents` is one
:class:`FilterSpec`: it turns its query value into a SQLAlchemy boolean condition, or
``None`` when the value is unset. :func:`build_specs` maps a ``DocumentListQuery`` onto the
spec list and the service AND-combines the non-``None`` conditions — so a **new filter is a
new** ``FilterSpec`` **subclass plus one line in** :func:`build_specs`, never another ``if`` in
the query body.

This shares the open-for-extension goal of ``tags._JOIN_SPECS`` — add a case, don't edit a
central branch — but as a class per filter rather than a data table: unlike the uniform
join-table lookups there, each filter here is distinct logic (``ilike`` escaping,
``inclusive_end``, the latest-status subquery, an async tag lookup). Its sibling
:mod:`api.services.job_filters` filters the ingestion queue the same way; both build on the
``FilterSpec`` base and ``to_conditions`` combiner in :mod:`api.services.filters`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import documents as doc_t
from api.db import job_records as job_t
from api.models.document import DocumentListQuery
from api.services.filters import FilterSpec, ilike_contains, inclusive_end


def latest_status_subquery() -> Any:
    """Correlated scalar subquery: the most recent job status for each ``documents`` row.

    A document accrues several ``job_records`` as it re-ingests/retries; this picks the newest
    one's status. Shared by ``list_documents`` (to project the ``status`` column) and
    :class:`StatusSpec` (to filter on it) so the subquery is defined once.
    """
    return (
        select(job_t.c.status)
        .where(job_t.c.document_id == doc_t.c.id)
        .order_by(job_t.c.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )


class FilenameSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return ilike_contains(doc_t.c.filename, self.value) if self.value else None


class FileTypeSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return doc_t.c.file_type == self.value if self.value else None


class StatusSpec(FilterSpec):
    """Matches on the *latest* job status (see :func:`latest_status_subquery`)."""

    async def to_condition(self, db: AsyncSession) -> Any | None:
        return latest_status_subquery() == self.value if self.value else None


class IndexedSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        if self.value is None:
            return None
        # "indexed" mirrors the emitted flag bool(chunk_count): a done-with-zero-chunks document
        # (chunk_count == 0) is NOT indexed, same as a not-yet-ingested one (chunk_count IS NULL).
        if self.value:
            return doc_t.c.chunk_count > 0
        return or_(doc_t.c.chunk_count.is_(None), doc_t.c.chunk_count == 0)


class EmbeddingModelSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return doc_t.c.embedding_model == self.value if self.value else None


class StorageBackendSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return doc_t.c.storage_backend == self.value if self.value else None


class MinSizeSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        # `is not None`, not truthiness: a 0-byte bound is a real filter.
        return doc_t.c.file_size >= self.value if self.value is not None else None


class MaxSizeSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return doc_t.c.file_size <= self.value if self.value is not None else None


class CreatedAfterSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return doc_t.c.created_at >= self.value if self.value else None


class CreatedBeforeSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        # inclusive_end expands a date-only bound to end-of-day so the bound day is covered.
        return doc_t.c.created_at <= inclusive_end(self.value) if self.value else None


class UpdatedAfterSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return doc_t.c.updated_at >= self.value if self.value else None


class UpdatedBeforeSpec(FilterSpec):
    async def to_condition(self, db: AsyncSession) -> Any | None:
        return doc_t.c.updated_at <= inclusive_end(self.value) if self.value else None


class TagSpec(FilterSpec):
    """AND filter: documents carrying *every* named tag (resolved to IDs via a DB lookup)."""

    async def to_condition(self, db: AsyncSession) -> Any | None:
        if not self.value:
            return None
        # Local import: api.services.tags pulls in collections/workspaces, a cycle at module load.
        from api.services.tags import matching_entity_ids

        return doc_t.c.id.in_(await matching_entity_ids("document", self.value, db))


def build_specs(query: DocumentListQuery) -> list[FilterSpec]:
    """Every filter carried by ``query`` as a spec. Add a filter here, not as an ``if`` in the query body."""
    return [
        FilenameSpec(query.filename),
        FileTypeSpec(query.file_type),
        StatusSpec(query.status),
        IndexedSpec(query.indexed),
        EmbeddingModelSpec(query.embedding_model),
        StorageBackendSpec(query.storage_backend),
        MinSizeSpec(query.min_size),
        MaxSizeSpec(query.max_size),
        CreatedAfterSpec(query.created_after),
        CreatedBeforeSpec(query.created_before),
        UpdatedAfterSpec(query.updated_after),
        UpdatedBeforeSpec(query.updated_before),
        TagSpec(query.tag),
    ]
