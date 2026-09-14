"""Add the missing 'scheduled' value to the content_status enum.

Live verification surfaced that the ContentStatus model enum includes
SCHEDULED but migration 001's content_status DB enum never had the value, so
any live read/write of a scheduled post would fail with
InvalidTextRepresentationError.

Revision ID: 013
Revises: 012
Create Date: 2026-09-07 00:00:00.000000
"""

from alembic import op


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE content_status ADD VALUE IF NOT EXISTS 'scheduled'")


def downgrade() -> None:
    # Postgres cannot remove a value from an enum type; recreate without it.
    op.execute("ALTER TYPE content_status RENAME TO content_status_old")
    op.execute(
        "CREATE TYPE content_status AS ENUM ('draft','published','archived','flagged','removed')"
    )
    op.execute(
        """
        ALTER TABLE posts
        ALTER COLUMN status TYPE content_status
        USING status::text::content_status
        """
    )
    op.execute("DROP TYPE content_status_old")
