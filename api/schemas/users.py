"""Request schemas for the users + permissions management endpoints."""

from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field, StringConstraints


def _canonical_email(email: str) -> str:
    """Fold an address to one canonical form so uniqueness is case-insensitive.

    ``EmailStr`` already lower-cases the domain; this also lower-cases the local part,
    so ``Jane@Example.com`` and ``jane@example.com`` are stored — and rejected as
    duplicates by the ``UNIQUE(email)`` check in the users service — as the same address.
    """
    return email.lower()


# EmailStr validates + normalizes the domain; AfterValidator then folds case fully.
CanonicalEmail = Annotated[EmailStr, AfterValidator(_canonical_email)]


def _blank_to_none(v: Any) -> Any:
    """Treat a blank/whitespace-only email as absent so it is stored as NULL, not ``''``.

    Email is optional (an account is identified by its ``username``); coercing blank to
    ``None`` keeps the ``UNIQUE(email)`` constraint from tripping over multiple ``''`` rows.
    """
    if isinstance(v, str) and not v.strip():
        return None
    return v


# Optional address: a blank or omitted value validates to None; a present value must still
# be a real, canonicalised email. BeforeValidator runs first so "" bypasses EmailStr.
OptionalEmail = Annotated[CanonicalEmail | None, BeforeValidator(_blank_to_none)]

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

# Per-user MCP rate-limit override (requests/min). 0 = inherit the global
# mcp.rate_limit_rpm; a positive value caps this user's key. Bounded ge=0 so a bad
# value is a clean 422 (mirrors MCPConfig.rate_limit_rpm's ge=1 at the config boundary).
RateLimitRpm = Annotated[int, Field(ge=0)]


class UserCreate(BaseModel):
    """Body for POST /users."""

    username: Username
    email: OptionalEmail = None
    name: str = ""
    is_active: bool = True
    is_admin: bool = False
    rate_limit_rpm: RateLimitRpm = 0


class UserUpdate(BaseModel):
    """Body for PATCH /users/{user_id} — only non-null fields are applied."""

    username: Username | None = None
    email: OptionalEmail = None
    name: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None
    rate_limit_rpm: RateLimitRpm | None = None


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
