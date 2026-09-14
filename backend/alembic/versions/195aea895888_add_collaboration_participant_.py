"""Add collaboration participant uniqueness constraint.

Revision ID: 195aea895888
Revises: 5992447b5c45
Create Date: 2026-09-12
"""

from alembic import op


revision = "195aea895888"
down_revision = "5992447b5c45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_collaboration_participant",
        "collaboration_participants",
        ["collaboration_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_collaboration_participant",
        "collaboration_participants",
        type_="unique",
    )