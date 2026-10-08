"""Add collaboration participant uniqueness constraint.

Revision ID: 195aea895888
Revises: 5992447b5c45
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa


revision = "195aea895888"
down_revision = "5992447b5c45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    constraints = sa.inspect(op.get_bind()).get_unique_constraints("collaboration_participants")
    for constraint in constraints:
        columns = set(constraint["column_names"])
        if columns == {"collaboration_id", "user_id"}:
            return
        if constraint["name"] == "uq_collaboration_participant":
            raise RuntimeError("Collaboration participant constraint has an unexpected definition")
    op.create_unique_constraint(
        "uq_collaboration_participant",
        "collaboration_participants",
        ["collaboration_id", "user_id"],
    )


def downgrade() -> None:
    # Revision 001 already requires this constraint; preserve the baseline invariant.
    pass