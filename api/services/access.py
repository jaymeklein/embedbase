"""Access policies — composable authorization + existence checks (the policy pattern).

Every check a route needs before it acts is an :class:`AccessPolicy`: a small value object
with one ``apply(db, principal)`` method that **raises** ``HTTPException`` to deny the request
(and returns ``None`` to allow it). :class:`CompositePolicy` bundles several into one — it
applies them **in order** and lets the first denial propagate — and is itself an
:class:`AccessPolicy`, so composites nest.

A route states its access requirement declaratively::

    await CompositePolicy(
        AuthorizeDocument(doc_id, "write"),     # 403 if the caller may not write it
        CollectionInWorkspace(ws_id, col_id),   # 404 if the URL's collection path is wrong
    ).apply(db, principal)

**Order is a security property.** Authorization policies come first, existence policies second,
so a scoped caller who may not reach a resource gets a uniform ``403`` whether or not it exists
— the ``404`` can never be used as an existence oracle. This is also why ``apply`` *raises*
rather than returning a ``bool`` (as in the canonical pattern): the policies surface **different**
status codes (403 vs 404), and which one fires first is exactly what closes the oracle — a single
bool could carry neither. Raising also reuses the authority's own errors verbatim.

The policies only *compose* the single authorization authority
(:mod:`api.services.permissions`) with the domain existence checks
(:mod:`api.services.collections`); they hold no scope logic of their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from api.services import collections as collection_svc
from api.services import permissions
from api.services.auth import Principal


@runtime_checkable
class AccessPolicy(Protocol):
    """One access rule. ``apply`` raises ``HTTPException`` to deny, returns ``None`` to allow."""

    async def apply(self, db: AsyncSession, principal: Principal) -> None: ...


@dataclass(frozen=True)
class AuthorizeWorkspace:
    """The caller may ``need`` (read/write) the workspace (else ``403``)."""

    ws_id: str
    need: permissions.Level = "read"

    async def apply(self, db: AsyncSession, principal: Principal) -> None:
        await permissions.authorize_workspace(db, principal, self.ws_id, self.need)


@dataclass(frozen=True)
class AuthorizeCollection:
    """The caller may ``need`` (read/write) the collection (else ``403``)."""

    col_id: str
    need: permissions.Level = "read"

    async def apply(self, db: AsyncSession, principal: Principal) -> None:
        await permissions.authorize_collection(db, principal, self.col_id, self.need)


@dataclass(frozen=True)
class AuthorizeDocument:
    """The caller may ``need`` (read/write) the document (else ``403``)."""

    doc_id: str
    need: permissions.Level = "read"

    async def apply(self, db: AsyncSession, principal: Principal) -> None:
        await permissions.authorize_document(db, principal, self.doc_id, self.need)


@dataclass(frozen=True)
class CollectionInWorkspace:
    """The collection exists inside the workspace named in the URL path (else ``404``)."""

    ws_id: str
    col_id: str

    async def apply(self, db: AsyncSession, principal: Principal) -> None:
        await collection_svc.require_collection(self.ws_id, self.col_id, db)


class CompositePolicy:
    """Apply several policies in order; the first that denies raises. Itself an ``AccessPolicy``."""

    def __init__(self, *policies: AccessPolicy) -> None:
        # Fail closed: an empty composite would authorize everything (empty loop → allow).
        if not policies:
            raise ValueError("CompositePolicy requires at least one policy")
        self._policies = policies

    async def apply(self, db: AsyncSession, principal: Principal) -> None:
        for policy in self._policies:
            await policy.apply(db, principal)
