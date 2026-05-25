import secrets
from datetime import UTC, datetime
from uuid import uuid4

import aiosqlite
import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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


async def _require_workspace(ws_id: str, db: aiosqlite.Connection) -> None:
    row = await (await db.execute("SELECT id FROM workspaces WHERE id = ?", (ws_id,))).fetchone()
    if not row:
        raise HTTPException(404, f"Workspace {ws_id!r} not found")


@router.post("", status_code=201)
async def create_collection(ws_id: str, body: CollectionCreate, db: aiosqlite.Connection = Depends(get_db)):
    await _require_workspace(ws_id, db)
    col_id = f"col_{uuid4().hex[:12]}"
    now = datetime.now(UTC).isoformat()
    try:
        await db.execute(
            "INSERT INTO collections (id, workspace_id, name, description, color, icon, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (col_id, ws_id, body.name, body.description, body.color, body.icon, now, now),
        )
        await db.commit()
    except aiosqlite.IntegrityError:
        raise HTTPException(409, f"Collection {body.name!r} already exists in this workspace") from None
    return {"id": col_id, "workspace_id": ws_id, "name": body.name, "description": body.description,
            "color": body.color, "icon": body.icon, "created_at": now, "updated_at": now,
            "document_count": 0, "chunk_count": 0}


@router.get("")
async def list_collections(ws_id: str, db: aiosqlite.Connection = Depends(get_db)):
    await _require_workspace(ws_id, db)
    rows = await (await db.execute(
        "SELECT c.*, COUNT(d.id) as document_count "
        "FROM collections c LEFT JOIN documents d ON d.collection_id = c.id "
        "WHERE c.workspace_id = ? GROUP BY c.id ORDER BY c.created_at",
        (ws_id,),
    )).fetchall()
    return [dict(r) for r in rows]


@router.get("/{col_id}")
async def get_collection(ws_id: str, col_id: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute(
        "SELECT * FROM collections WHERE id = ? AND workspace_id = ?", (col_id, ws_id)
    )).fetchone()
    if not row:
        raise HTTPException(404, f"Collection {col_id!r} not found")
    return dict(row)


@router.patch("/{col_id}")
async def update_collection(ws_id: str, col_id: str, body: CollectionUpdate,
                            db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute(
        "SELECT * FROM collections WHERE id = ? AND workspace_id = ?", (col_id, ws_id)
    )).fetchone()
    if not row:
        raise HTTPException(404, f"Collection {col_id!r} not found")
    now = datetime.now(UTC).isoformat()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    await db.execute(f"UPDATE collections SET {set_clause} WHERE id = ?", (*updates.values(), col_id))
    await db.commit()
    row = await (await db.execute("SELECT * FROM collections WHERE id = ?", (col_id,))).fetchone()
    return dict(row)


@router.delete("/{col_id}", status_code=204)
async def delete_collection(ws_id: str, col_id: str, db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute(
        "SELECT id FROM collections WHERE id = ? AND workspace_id = ?", (col_id, ws_id)
    )).fetchone()
    if not row:
        raise HTTPException(404, f"Collection {col_id!r} not found")
    # Vector store cleanup added in Delivery 2
    await db.execute("DELETE FROM collections WHERE id = ?", (col_id,))
    await db.commit()


# ── API key management ────────────────────────────────────────────────────────

@router.post("/{col_id}/keys", status_code=201)
async def create_api_key(ws_id: str, col_id: str, body: APIKeyCreate,
                         db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute(
        "SELECT id FROM collections WHERE id = ? AND workspace_id = ?", (col_id, ws_id)
    )).fetchone()
    if not row:
        raise HTTPException(404, f"Collection {col_id!r} not found")

    raw_key = "eb_" + secrets.token_urlsafe(32)
    key_prefix = raw_key[3:11]  # 8 chars after "eb_"
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()
    key_id = uuid4().hex
    now = datetime.now(UTC).isoformat()

    await db.execute(
        "INSERT INTO api_keys (id, collection_id, key_prefix, key_hash, label, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (key_id, col_id, key_prefix, key_hash, body.label, now),
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
async def list_api_keys(ws_id: str, col_id: str, db: aiosqlite.Connection = Depends(get_db)):
    rows = await (await db.execute(
        "SELECT id, collection_id, key_prefix, label, created_at, last_used_at "
        "FROM api_keys WHERE collection_id = ? ORDER BY created_at",
        (col_id,),
    )).fetchall()
    return [dict(r) for r in rows]


@router.delete("/{col_id}/keys/{key_id}", status_code=204)
async def revoke_api_key(ws_id: str, col_id: str, key_id: str,
                         db: aiosqlite.Connection = Depends(get_db)):
    row = await (await db.execute(
        "SELECT id FROM api_keys WHERE id = ? AND collection_id = ?", (key_id, col_id)
    )).fetchone()
    if not row:
        raise HTTPException(404, f"API key {key_id!r} not found")
    await db.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    await db.commit()
