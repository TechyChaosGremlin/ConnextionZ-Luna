"""Create the existing profile resolver's missing playlist table.

Revision ID: 213
Revises: 212
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "213"
down_revision = "212"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("playlists"):
        required = {
            "id", "profile_id", "title", "cover", "item_label",
            "plays", "created_at", "updated_at",
        }
        existing = {column["name"] for column in inspector.get_columns("playlists")}
        if not required.issubset(existing):
            raise RuntimeError("Existing playlists table does not match the profile model")
        return

    op.create_table(
        "playlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("cover", sa.String(500), nullable=False),
        sa.Column("item_label", sa.String(80), nullable=False),
        sa.Column("plays", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_playlists_profile_id", "playlists", ["profile_id"])


def downgrade() -> None:
    # Keep existing profile data; this table may predate the reconciliation.
    pass
