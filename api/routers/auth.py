"""Console login endpoints — username + password → a JWT session.

Public ``POST /auth/login`` issues a short-lived bearer token. The self-service
``POST /auth/change-password`` and ``GET /auth/me`` require a live session
(``require_operator``, which also admits a must-change session so a first-login
user can set their password). Token + password mechanics live in
:mod:`api.services.session`; user lookups in :mod:`api.services.users`.
"""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas.auth import ChangePasswordRequest, LoginRequest
from api.services import permissions, session
from api.services import users as user_svc
from api.services.auth import Principal, require_operator

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """Verify username + password and return a session token (coarse 401 on failure)."""
    result = await user_svc.authenticate_login(body.username, body.password, db)
    token = session.mint_session(
        result["user_id"], must_change=result["must_change"], pwd_epoch=result["pwd_epoch"]
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "must_change_password": result["must_change"],
    }


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    """Change the current user's password, returning a fresh token (prior sessions die)."""
    # require_operator guarantees a user identity — narrow the Optional for the type checker.
    user_id = cast(str, principal.user_id)
    result = await user_svc.change_password(
        user_id, body.current_password, body.new_password, db
    )
    token = session.mint_session(result["user_id"], must_change=False, pwd_epoch=result["pwd_epoch"])
    return {"access_token": token, "token_type": "bearer", "must_change_password": False}


@router.get("/me")
async def me(
    principal: Principal = Depends(require_operator),
    db: AsyncSession = Depends(get_db),
):
    """The current console user (identity + is_admin + must_change_password + capabilities)."""
    # require_operator guarantees a user identity — narrow the Optional for the type checker.
    user_id = cast(str, principal.user_id)
    user = await user_svc.get_user(user_id, db)
    user["can_create_workspaces"] = await permissions.has_capability(
        db, principal, permissions.CAP_CREATE_WORKSPACE
    )
    return user
