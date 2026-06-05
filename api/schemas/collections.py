"""Request/response schemas for the collections and API-key endpoints."""

from pydantic import BaseModel


class CollectionCreate(BaseModel):
    """Body for POST /workspaces/{ws_id}/collections."""

    name: str
    description: str = ""
    color: str = "#8b5cf6"
    icon: str = "book"


class CollectionUpdate(BaseModel):
    """Body for PATCH /workspaces/{ws_id}/collections/{col_id}."""

    name: str | None = None
    description: str | None = None
    color: str | None = None
    icon: str | None = None


class APIKeyCreate(BaseModel):
    """Body for POST /workspaces/{ws_id}/collections/{col_id}/keys."""

    label: str = ""
