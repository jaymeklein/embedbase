"""User + permission management endpoints (master-only).

Routing-only: path registration, dependency resolution, delegation. All logic
lives in api/services/users.py (users + keys) and api/services/permissions.py
(grants). The whole router is gated by the master key so a later-added endpoint
can't be accidentally unauthenticated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.users import GrantCreate, UserCreate, UserKeyCreate, UserUpdate
from api.services import users as user_svc
from api.services.auth import require_master

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_master)])


@router.post("", status_code=201)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create a user. Mint their API key separately via POST /users/{id}/key."""
    return await user_svc.create_user(body.email, body.name, body.is_active, db)


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db)):
    """List all users with their API-key summary (never the secret)."""
    return await user_svc.list_users(db)


@router.get("/{user_id}")
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    """Return one user with its API-key summary."""
    return await user_svc.get_user(user_id, db)


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, db: AsyncSession = Depends(get_db)):
    """Update name/email or activate/deactivate a user (an inactive user's key stops working)."""
    return await user_svc.update_user(user_id, body, db)


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a user; cascades to their key and grants."""
    await user_svc.delete_user(user_id, db)


# ── API key (one per user) ────────────────────────────────────────────────────

@router.post("/{user_id}/key", status_code=201)
async def create_user_key(
    user_id: str, body: UserKeyCreate, db: AsyncSession = Depends(get_db)
):
    """Mint (or rotate) the user's single API key; the raw key is returned once."""
    return await user_svc.mint_user_key(user_id, body.label, db)


@router.delete("/{user_id}/key", status_code=204)
async def revoke_user_key(user_id: str, db: AsyncSession = Depends(get_db)):
    """Revoke the user's API key."""
    await user_svc.revoke_user_key(user_id, db)


# ── Permission grants ─────────────────────────────────────────────────────────

@router.get("/{user_id}/permissions")
async def list_user_permissions(user_id: str, db: AsyncSession = Depends(get_db)):
    """List the resources this user is granted access to."""
    return await user_svc.list_user_permissions(user_id, db)


@router.post("/{user_id}/permissions", status_code=201)
async def add_user_permission(
    user_id: str, body: GrantCreate, db: AsyncSession = Depends(get_db)
):
    """Grant the user read/write on a workspace, collection, or document."""
    return await user_svc.add_permission(user_id, body, db)


@router.delete("/{user_id}/permissions/{grant_id}", status_code=204)
async def remove_user_permission(
    user_id: str, grant_id: str, db: AsyncSession = Depends(get_db)
):
    """Revoke one of the user's grants."""
    await user_svc.remove_permission(user_id, grant_id, db)
