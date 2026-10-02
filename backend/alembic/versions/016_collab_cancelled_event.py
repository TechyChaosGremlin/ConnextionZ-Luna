"""Add the collaboration-cancelled analytics event.

Revision ID: 016
Revises: 015
"""

from alembic import op


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE analytics_event_type ADD VALUE IF NOT EXISTS 'collab_cancelled'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values; the extra label is backward-compatible.
    pass