"""Request schemas for the users + permissions management endpoints."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, EmailStr, StringConstraints


def _canonical_email(email: str) -> str:
    """Fold an address to one canonical form so uniqueness is case-insensitive.

    ``EmailStr`` already lower-cases the domain; this also lower-cases the local part,
    so ``Jane@Example.com`` and ``jane@example.com`` are stored — and rejected as
    duplicates by the ``UNIQUE(email)`` check in the users service — as the same address.
    """
    return email.lower()


# EmailStr validates + normalizes the domain; AfterValidator then folds case fully.
CanonicalEmail = Annotated[EmailStr, AfterValidator(_canonical_email)]

# Login id: trimmed + lower-cased (case-insensitive login, matches the backfill of
# username=email for pre-existing rows). ``@``/``+`` allowed so an email works as one.
Username = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9._+@-]+$",
    ),
]


class UserCreate(BaseModel):
    """Body for POST /users."""

    username: Username
    email: CanonicalEmail
    name: str = ""
    is_active: bool = True
    is_admin: bool = False


class UserUpdate(BaseModel):
    """Body for PATCH /users/{user_id} — only non-null fields are applied."""

    username: Username | None = None
    email: CanonicalEmail | None = None
    name: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class UserKeyCreate(BaseModel):
    """Body for POST /users/{user_id}/key (mint or rotate the user's single key)."""

    label: str = ""


class GrantCreate(BaseModel):
    """Body for POST /users/{user_id}/permissions.

    ``capability`` grants a non-resource privilege (e.g. ``resource_id="create_workspace"``);
    the other types scope access to a workspace/collection/document.
    """

    resource_type: Literal["workspace", "collection", "document", "capability"]
    resource_id: str
    level: Literal["read", "write"]
