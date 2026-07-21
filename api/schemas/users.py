"""Request schemas for the users + permissions management endpoints."""

from typing import Literal

from pydantic import BaseModel


class UserCreate(BaseModel):
    """Body for POST /users."""

    email: str
    name: str = ""
    is_active: bool = True


class UserUpdate(BaseModel):
    """Body for PATCH /users/{user_id} — only non-null fields are applied."""

    email: str | None = None
    name: str | None = None
    is_active: bool | None = None


class UserKeyCreate(BaseModel):
    """Body for POST /users/{user_id}/key (mint or rotate the user's single key)."""

    label: str = ""


class GrantCreate(BaseModel):
    """Body for POST /users/{user_id}/permissions."""

    resource_type: Literal["workspace", "collection", "document"]
    resource_id: str
    level: Literal["read", "write"]
