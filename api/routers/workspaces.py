from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.db import collections as col_t
from api.db import workspaces as ws_t
from api.dependencies import get_db

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceCreate(BaseModel):
    name: str
    description: str = ""
    color: str = "#6366f1"
    icon: str = "folder"


class WorkspaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon: str | None = None


@router.post("", status_code=201)
async def create_workspace(
    body: WorkspaceCreate, db: AsyncSession = Depends(get_db)
):
    ws_id = f"ws_{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    await db.execute(
        insert(ws_t).values(
            id=ws_id,
            name=body.name,
            description=body.description,
            color=body.color,
            icon=body.icon,
            created_at=now,
            updated_at=now,
        )
    )
    await db.commit()
    return {
        "id": ws_id,
        "name": body.name,
        "description": body.description,
        "color": body.color,
        "icon": body.icon,
        "created_at": now,
        "updated_at": now,
        "collection_count": 0,
        "document_count": 0,
        "chunk_count": 0,
    }


@router.get("")
async def list_workspaces(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(ws_t, func.count(col_t.c.id).label("collection_count"))
        .outerjoin(col_t, col_t.c.workspace_id == ws_t.c.id)
        .group_by(ws_t.c.id)
        .order_by(ws_t.c.created_at.desc())
    )
    result = await db.execute(stmt)
    return [dict(row._mapping) for row in result.fetchall()]


@router.get("/{ws_id}")
async def get_workspace(ws_id: str, db: AsyncSession = Depends(get_db)):
    row = (
        await db.execute(select(ws_t).where(ws_t.c.id == ws_id))
    ).fetchone()
    if not row:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")

    cols = (
        await db.execute(
            select(col_t)
            .where(col_t.c.workspace_id == ws_id)
            .order_by(col_t.c.created_at)
        )
    ).fetchall()

    result = dict(row._mapping)
    result["collections"] = [dict(c._mapping) for c in cols]
    return result


@router.patch("/{ws_id}")
async def update_workspace(
    ws_id: str, body: WorkspaceUpdate, db: AsyncSession = Depends(get_db)
):
    exists = (
        await db.execute(select(ws_t.c.id).where(ws_t.c.id == ws_id))
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        # Nothing to change — return current state
        row = (await db.execute(select(ws_t).where(ws_t.c.id == ws_id))).fetchone()
        assert row is not None
        return dict(row._mapping)

    updates["updated_at"] = datetime.now(UTC).isoformat()
    await db.execute(update(ws_t).where(ws_t.c.id == ws_id).values(**updates))
    await db.commit()

    row = (await db.execute(select(ws_t).where(ws_t.c.id == ws_id))).fetchone()
    assert row is not None
    return dict(row._mapping)


@router.delete("/{ws_id}", status_code=204)
async def delete_workspace(ws_id: str, db: AsyncSession = Depends(get_db)):
    exists = (
        await db.execute(select(ws_t.c.id).where(ws_t.c.id == ws_id))
    ).fetchone()
    if not exists:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")

    # SQLite ON DELETE CASCADE removes collections, api_keys, documents, job_records.
    # Vector store + BM25 cleanup hooks added in Delivery 2.
    await db.execute(delete(ws_t).where(ws_t.c.id == ws_id))
    await db.commit()
