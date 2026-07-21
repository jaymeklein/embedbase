"""Permissions table — per-user access grants over the resource hierarchy.

One row grants a user a ``level`` (``read`` or ``write``) on a single resource
(``resource_type`` ∈ ``workspace`` | ``collection`` | ``document``). Grants are
**hierarchical**: a grant on a workspace covers its collections and documents; a
grant on a collection covers its documents. ``write`` implies ``read``.

``resource_id`` is a plain string with no foreign key — it is polymorphic across
three tables (like ``job_records.document_id``). A grant left dangling by a
deleted resource is harmless: the id is a random uuid that is never reissued.
"""

from sqlalchemy import Column, ForeignKey, Index, String, Table, UniqueConstraint

from api.tables.metadata import metadata

permissions = Table(
    "permissions",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "user_id",
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("resource_type", String, nullable=False),
    Column("resource_id", String, nullable=False),
    Column("level", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "user_id", "resource_type", "resource_id", name="permissions_user_resource_unique"
    ),
    Index("permissions_user_idx", "user_id"),
)
