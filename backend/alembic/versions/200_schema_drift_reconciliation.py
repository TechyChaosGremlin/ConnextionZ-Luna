"""Reconcile indexes and analytics event enum labels after baseline stamping.

This revision assumes the existing populated schema has been stamped at both
current heads (018 and 199). It repairs missing objects without replaying the
table-creation migrations. The pgvector IVFFlat indexes are intentionally
deferred until their embedding tables contain data.

Revision ID: 200
Revises: 018, 199
"""

import re

from alembic import op
import sqlalchemy as sa


revision = "200"
down_revision = ("018", "199")
branch_labels = None
depends_on = None


_INDEXES = (
    {
        "name": "ix_posts_status_created_at",
        "table": "posts",
        "columns": ("status", "created_at"),
    },
    {
        "name": "ix_analytics_events_post_type_created",
        "table": "analytics_events",
        "columns": ("post_id", "event_type", "created_at"),
    },
    {
        "name": "ix_analytics_events_target_type_created",
        "table": "analytics_events",
        "columns": ("target_user_id", "event_type", "created_at"),
    },
    {
        "name": "ix_posts_tags_gin",
        "table": "posts",
        "columns": ("tags",),
        "using": "gin",
    },
    {
        "name": "ix_stream_destinations_platform",
        "table": "stream_destinations",
        "columns": ("platform",),
    },
    {
        "name": "uq_stream_sessions_live_owner",
        "table": "stream_sessions",
        "columns": ("owner_id",),
        "unique": True,
        "where": "status IN ('pending', 'active')",
    },
)

_ANALYTICS_EVENT_VALUES = (
    "collab_accepted",
    "collab_declined",
    "collab_cancelled",
    "collab_completed",
    "collab_started",
)


def _normalize_predicate(predicate: object | None) -> str | None:
    if predicate is None:
        return None

    normalized = str(predicate).lower().replace('"', "")
    normalized = re.sub(r"::[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?", "", normalized)
    normalized = re.sub(
        r"\bstatus\s*=\s*any\s*\(\s*array\s*\[\s*"
        r"('[^']*'(?:\s*,\s*'[^']*')*)\s*\]\s*\)",
        r"status in(\1)",
        normalized,
    )
    return re.sub(r"[\s()\[\]]", "", normalized)


def _matches_index(index: dict, specification: dict) -> bool:
    dialect_options = index.get("dialect_options", {})
    using = dialect_options.get("postgresql_using", "btree") or "btree"
    where = dialect_options.get("postgresql_where")

    return (
        tuple(index.get("column_names") or ()) == specification["columns"]
        and bool(index.get("unique")) == bool(specification.get("unique", False))
        and using == specification.get("using", "btree")
        and _normalize_predicate(where)
        == _normalize_predicate(specification.get("where"))
    )


def _ensure_index(bind, specification: dict) -> None:
    indexes = sa.inspect(bind).get_indexes(specification["table"])
    equivalent = []

    for index in indexes:
        if _matches_index(index, specification):
            equivalent.append(index)
        elif index.get("name") == specification["name"]:
            raise RuntimeError(
                f"Index {specification['name']} already exists with a different definition"
            )

    if equivalent:
        return

    options = {
        "unique": specification.get("unique", False),
        "postgresql_using": specification.get("using", "btree"),
    }
    if "where" in specification:
        options["postgresql_where"] = sa.text(specification["where"])

    op.create_index(
        specification["name"],
        specification["table"],
        list(specification["columns"]),
        **options,
    )


def upgrade() -> None:
    bind = op.get_bind()
    for specification in _INDEXES:
        _ensure_index(bind, specification)

    # PostgreSQL requires enum additions to run outside a transaction block.
    # ADD VALUE IF NOT EXISTS allows databases with previously-added labels.
    with op.get_context().autocommit_block():
        for value in _ANALYTICS_EVENT_VALUES:
            op.execute(
                "ALTER TYPE analytics_event_type "
                f"ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    # Do not drop indexes: this revision may have skipped equivalent indexes
    # created by the historical migrations, so their ownership is ambiguous.
    # PostgreSQL also does not support safely removing enum values with a
    # simple downgrade; the five analytics_event_type labels are retained.
    pass
