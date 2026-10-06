"""Limit each owner to one pending or active streaming session.

Revision ID: 199
Revises: 198
"""

from alembic import op
import sqlalchemy as sa

revision = "199"
down_revision = "198"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_stream_sessions_live_owner"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "stream_sessions",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'active')"),
        sqlite_where=sa.text("status IN ('pending', 'active')"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="stream_sessions")
