from datetime import UTC, datetime

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
async def create_workspace(body: WorkspaceCreate, db: aiosqlite.Connection = Depends(get_db)):
    from uuid import uuid4
    ws_id = f"ws_{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    await db.execute(
        "INSERT INTO workspaces (id, name, description, color, icon, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ws_id, body.name, body.description, body.color, body.icon, now, now),
    )
    await db.commit()
    return {"id": ws_id, "name": body.name, "description": body.description,
            "color": body.color, "icon": body.icon, "created_at": now, "updated_at": now,
            "collection_count": 0, "document_count": 0, "chunk_count": 0}


@router.get("")
async def list_workspaces(db: aiosqlite.Connection = Depends(get_db)):
    rows = await (await db.execute(
        "SELECT w.*, COUNT(c.id) as collection_count "
        "FROM workspaces w LEFT JOIN collections c ON c.workspace_id = w.id "
        "GROUP BY w.id ORDER BY w.created_at DESC"
    )).fetchall()
    return [dict(r) for r in rows]


@router.get("/{ws_id}")
async def get_workspace(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute(
        "SELECT * FROM workspaces WHERE id = ?", (ws_id,)
    )).fetchone()
    if not row:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")
    collections = await (await db.execute(
        "SELECT * FROM collections WHERE workspace_id = ? ORDER BY created_at", (ws_id,)
    )).fetchall()
    result = dict(row)
    result["collections"] = [dict(c) for c in collections]
    return result


@router.patch("/{ws_id}")
async def update_workspace(ws_id: str, body: WorkspaceUpdate, db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,))).fetchone()
    if not row:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")
    now = datetime.now(UTC).isoformat()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    await db.execute(f"UPDATE workspaces SET {set_clause} WHERE id = ?",
                     (*updates.values(), ws_id))
    await db.commit()
    row = await (await db.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,))).fetchone()
    return dict(row)


@router.delete("/{ws_id}", status_code=204)
async def delete_workspace(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute("SELECT id FROM workspaces WHERE id = ?", (ws_id,))).fetchone()
    if not row:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")
    # Cascade handled by SQLite ON DELETE CASCADE
    # Vector store cleanup hook will be added in Delivery 2
    await db.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
    await db.commit()
