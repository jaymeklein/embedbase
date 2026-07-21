from api.tables.api_keys import api_keys
from api.tables.collections import collections
from api.tables.documents import documents
from api.tables.job_records import job_records
from api.tables.metadata import metadata
from api.tables.permissions import permissions
from api.tables.tags import (
    collection_tags,
    document_tags,
    tags,
    workspace_tags,
)
from api.tables.users import users
from api.tables.workspaces import workspaces

__all__ = [
    "metadata",
    "workspaces",
    "collections",
    "users",
    "api_keys",
    "permissions",
    "documents",
    "job_records",
    "tags",
    "workspace_tags",
    "collection_tags",
    "document_tags",
]
