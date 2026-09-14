"""GIN index on posts.tags to support the For You feed's interest-pool query
(tag overlap via the jsonb ?| operator over published posts).

Revision ID: 010
Revises: 009
Create Date: 2026-09-07 00:00:00.000000
"""

from alembic import op


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_posts_tags_gin",
        "posts",
        ["tags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_posts_tags_gin", table_name="posts")
