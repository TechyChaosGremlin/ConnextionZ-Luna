"""Add the collaboration-started analytics event.

Revision ID: 018
Revises: 017
"""

from alembic import op


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE analytics_event_type ADD VALUE IF NOT EXISTS 'collab_started'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values; the extra label is backward-compatible.
    pass