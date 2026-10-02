"""Add the collaboration-declined analytics event.

Revision ID: 015
Revises: 014
"""

from alembic import op


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE analytics_event_type ADD VALUE IF NOT EXISTS 'collab_declined'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values; the extra label is backward-compatible.
    pass