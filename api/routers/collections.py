import secrets
from datetime import UTC, datetime
from uuid import uuid4

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import api_keys as keys_t
from api.db import collections as col_t
from api.db import documents as doc_t
from api.db import workspaces as ws_t
from api.dependencies import get_db

router = APIRouter(prefix="/workspaces/{ws_id}/collections", tags=["collections"])


class CollectionCreate(BaseModel):
    name: str
    description: str = ""
    color: str = "#8b5cf6"
    icon: str = "book"


class CollectionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon: str | None = None


class APIKeyCreate(BaseModel):
    label: str = ""


async def _require_workspace(ws_id: str, db: AsyncSession) -> None:
    exists = (
        await db.execute(select(ws_t.c.id).where(ws_t.c.id == ws_id))
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")


@router.post("", status_code=201)
async def create_collection(
    ws_id: str, body: CollectionCreate, db: AsyncSession = Depends(get_db)
):
    await _require_workspace(ws_id, db)
    col_id = f"col_{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    try:
        await db.execute(
            insert(col_t).values(
                id=col_id,
                workspace_id=ws_id,
                name=body.name,
                description=body.description,
                color=body.color,
                icon=body.icon,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    except IntegrityError:
        # UNIQUE(workspace_id, name) violated
        await db.rollback()
        raise HTTPException(
            409,
            f"Collection {body.name!r} already exists in this workspace",
        ) from None
    return {
        "id": col_id,
        "workspace_id": ws_id,
        "name": body.name,
        "description": body.description,
        "color": body.color,
        "icon": body.icon,
        "created_at": now,
        "updated_at": now,
        "document_count": 0,
        "chunk_count": 0,
    }


@router.get("")
async def list_collections(ws_id: str, db: AsyncSession = Depends(get_db)):
    await _require_workspace(ws_id, db)
    stmt = (
        select(col_t, func.count(doc_t.c.id).label("document_count"))
        .outerjoin(doc_t, doc_t.c.collection_id == col_t.c.id)
        .where(col_t.c.workspace_id == ws_id)
        .group_by(col_t.c.id)
        .order_by(col_t.c.created_at)
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/{col_id}")
async def get_collection(
    ws_id: str, col_id: str, db: AsyncSession = Depends(get_db)
):
    row = (
        await db.execute(
            select(col_t).where(
                col_t.c.id == col_id, col_t.c.workspace_id == ws_id
            )
        )
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Collection {col_id!r} not found")
    return dict(row._mapping)


@router.patch("/{col_id}")
async def update_collection(
    ws_id: str,
    col_id: str,
    body: CollectionUpdate,
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            select(col_t.c.id).where(
                col_t.c.id == col_id, col_t.c.workspace_id == ws_id
            )
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Collection {col_id!r} not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        row = (
            await db.execute(select(col_t).where(col_t.c.id == col_id))
        ).fetchone()
        assert row is not None
        return dict(row._mapping)

    updates["updated_at"] = datetime.now(UTC).isoformat()
    await db.execute(
        update(col_t).where(col_t.c.id == col_id).values(**updates)
    )
    await db.commit()

    row = (
        await db.execute(select(col_t).where(col_t.c.id == col_id))
    ).fetchone()
    assert row is not None
    return dict(row._mapping)


@router.delete("/{col_id}", status_code=204)
async def delete_collection(
    ws_id: str, col_id: str, db: AsyncSession = Depends(get_db)
):
    exists = (
        await db.execute(
            select(col_t.c.id).where(
                col_t.c.id == col_id, col_t.c.workspace_id == ws_id
            )
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Collection {col_id!r} not found")

    # SQLite ON DELETE CASCADE removes api_keys, documents, job_records.
    # Vector store + BM25 cleanup added in Delivery 2.
    await db.execute(delete(col_t).where(col_t.c.id == col_id))
    await db.commit()


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

@router.post("/{col_id}/keys", status_code=201)
async def create_api_key(
    ws_id: str,
    col_id: str,
    body: APIKeyCreate,
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            select(col_t.c.id).where(
                col_t.c.id == col_id, col_t.c.workspace_id == ws_id
            )
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Collection {col_id!r} not found")

    raw_key = "eb_" + secrets.token_urlsafe(32)
    key_prefix = raw_key[3:11]  # 8 chars after "eb_" — stored for fast prefix lookup
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()
    key_id = uuid4().hex
    now = datetime.now(UTC).isoformat()

    await db.execute(
        insert(keys_t).values(
            id=key_id,
            collection_id=col_id,
            key_prefix=key_prefix,
            key_hash=key_hash,
            label=body.label,
            created_at=now,
        )
    )
    await db.commit()

    return {
        "id": key_id,
        "collection_id": col_id,
        "key_prefix": key_prefix,
        "label": body.label,
        "created_at": now,
        "raw_key": raw_key,  # shown ONCE — never retrievable again
    }


@router.get("/{col_id}/keys")
async def list_api_keys(
    ws_id: str, col_id: str, db: AsyncSession = Depends(get_db)
):
    # key_hash is NOT selected — never returned to clients
    stmt = (
        select(
            keys_t.c.id,
            keys_t.c.collection_id,
            keys_t.c.key_prefix,
            keys_t.c.label,
            keys_t.c.created_at,
            keys_t.c.last_used_at,
        )
        .where(keys_t.c.collection_id == col_id)
        .order_by(keys_t.c.created_at)
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.fetchall()]


@router.delete("/{col_id}/keys/{key_id}", status_code=204)
async def revoke_api_key(
    ws_id: str,
    col_id: str,
    key_id: str,
    db: AsyncSession = Depends(get_db),
):
    exists = (
        await db.execute(
            select(keys_t.c.id).where(
                keys_t.c.id == key_id, keys_t.c.collection_id == col_id
            )
        )
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"API key {key_id!r} not found")

    await db.execute(delete(keys_t).where(keys_t.c.id == key_id))
    await db.commit()
