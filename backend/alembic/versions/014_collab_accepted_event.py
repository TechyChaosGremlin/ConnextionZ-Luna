"""Add the collaboration-accepted analytics event.

Revision ID: 014
Revises: 013
"""

from alembic import op


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE analytics_event_type ADD VALUE IF NOT EXISTS 'collab_accepted'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values; the extra label is backward-compatible.
    pass