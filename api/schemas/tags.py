"""Request schemas for the tag CRUD, assignment, and merge endpoints."""

from pydantic import BaseModel


class TagCreate(BaseModel):
    """Body for POST /workspaces/{ws_id}/tags."""

    name: str
    color: str | None = None


class TagUpdate(BaseModel):
    """Body for PATCH /workspaces/{ws_id}/tags/{tag_id}."""

    name: str | None = None
    color: str | None = None


class TagMerge(BaseModel):
    """Body for POST /workspaces/{ws_id}/tags/merge.

    Repoints every assignment of ``source_id`` onto ``target_id`` and then
    deletes the now-empty source tag.
    """

    source_id: str
    target_id: str
