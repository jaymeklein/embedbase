from sqlalchemy import Column, ForeignKey, Index, String, Table, UniqueConstraint

from api.tables.metadata import metadata

# One API key per user: the key belongs to a user (UNIQUE(user_id)), and the
# user's grants in ``permissions`` decide what the key can reach. Auth narrows
# candidate rows by the indexed 8-char ``key_prefix``, then bcrypt-verifies the
# full secret (see api/services/auth.py).
api_keys = Table(
    "api_keys",
    metadata,
    Column("id", String, primary_key=True),
    Column(
        "user_id",
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("key_prefix", String, nullable=False),
    Column("key_hash", String, nullable=False),
    Column("label", String, nullable=False, server_default=""),
    Column("created_at", String, nullable=False),
    Column("last_used_at", String, nullable=True),
    UniqueConstraint("user_id", name="api_keys_user_unique"),
    Index("api_keys_prefix_idx", "key_prefix"),
)
