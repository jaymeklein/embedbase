"""Request schemas for the console-login endpoints (``/auth``).

Responses (the bearer token + ``must_change_password``, and the current user for
``/auth/me``) are returned as plain dicts by the router, matching the users API.
"""

from pydantic import BaseModel, Field

from api.schemas.users import Username


class LoginRequest(BaseModel):
    """Body for ``POST /auth/login``."""

    username: Username
    password: str


class ChangePasswordRequest(BaseModel):
    """Body for ``POST /auth/change-password``.

    ``new_password`` must meet the minimum length; ``current_password`` is the
    temp password (first login) or the existing one (voluntary change).
    """

    current_password: str
    new_password: str = Field(min_length=12)
