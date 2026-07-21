"""Request schemas for the users + permissions management endpoints."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr


def _canonical_email(email: str) -> str:
    """Fold an address to one canonical form so uniqueness is case-insensitive.

    ``EmailStr`` already lower-cases the domain; this also lower-cases the local part,
    so ``Jane@Example.com`` and ``jane@example.com`` are stored — and rejected as
    duplicates by the ``UNIQUE(email)`` check in the users service — as the same address.
    """
    return email.lower()


# EmailStr validates + normalizes the domain; AfterValidator then folds case fully.
CanonicalEmail = Annotated[EmailStr, AfterValidator(_canonical_email)]


class UserCreate(BaseModel):
    """Body for POST /users."""

    email: CanonicalEmail
    name: str = ""
    is_active: bool = True


class UserUpdate(BaseModel):
    """Body for PATCH /users/{user_id} — only non-null fields are applied."""

    email: CanonicalEmail | None = None
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
