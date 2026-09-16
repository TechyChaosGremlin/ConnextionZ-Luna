"""Add legacy profile compatibility fields.

Revision ID: 196
Revises: 195aea895888
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = "196"
down_revision = "195aea895888"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("avatar_color", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "profiles",
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "profiles",
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "profiles",
        sa.Column("collab_status", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "profiles",
        sa.Column("collab_score", sa.Float(), nullable=False, server_default="0.0"),
    )
    op.add_column(
        "profiles",
        sa.Column("open_to_collab", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "profiles",
        sa.Column("private_account", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "profiles",
        sa.Column(
            "response_time",
            sa.String(length=50),
            nullable=False,
            server_default="< 4 hours",
        ),
    )


def downgrade() -> None:
    op.drop_column("profiles", "response_time")
    op.drop_column("profiles", "private_account")
    op.drop_column("profiles", "open_to_collab")
    op.drop_column("profiles", "collab_score")
    op.drop_column("profiles", "collab_status")
    op.drop_column("profiles", "online")
    op.drop_column("profiles", "verified")
    op.drop_column("profiles", "avatar_color")