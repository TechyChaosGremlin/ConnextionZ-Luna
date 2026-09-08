"""Reconcile the posts table with the Post model.

Live verification against real Postgres surfaced that migration 001 never
created 11 columns the Post model has always defined (and which the For You
scoring, feed resolvers, and content reads depend on):
save_count, hashtags, audio, visibility, allow_comments, allow_collabs,
duration_sec, collab_with, moderation_status, thumbnail, media_url.

Without this, any live read/write of a Post fails with UndefinedColumnError.

Revision ID: 012
Revises: 011
Create Date: 2026-09-07 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


# (column name, column definition) in model-declared order.
_COLUMNS = [
    ("save_count", sa.Column("save_count", sa.Integer(), nullable=False, server_default="0")),
    ("thumbnail", sa.Column("thumbnail", sa.String(500), nullable=True)),
    ("media_url", sa.Column("media_url", sa.Text(), nullable=True)),
    ("hashtags", sa.Column("hashtags", postgresql.JSONB(), nullable=True)),
    ("audio", sa.Column("audio", sa.String(255), nullable=False, server_default="Original Sound")),
    ("visibility", sa.Column("visibility", sa.String(20), nullable=False, server_default="public")),
    ("allow_comments", sa.Column("allow_comments", sa.Boolean(), nullable=False, server_default=sa.true())),
    ("allow_collabs", sa.Column("allow_collabs", sa.Boolean(), nullable=False, server_default=sa.true())),
    ("duration_sec", sa.Column("duration_sec", sa.Float(), nullable=False, server_default="0")),
    ("collab_with", sa.Column("collab_with", sa.String(120), nullable=True)),
    ("moderation_status", sa.Column("moderation_status", sa.String(20), nullable=False, server_default="approved")),
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns("posts")}

    for name, column in _COLUMNS:
        if name not in existing:
            op.add_column("posts", column)

    # moderation_status carries an index in the model.
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("posts")}
    if "ix_posts_moderation_status" not in existing_indexes:
        op.create_index("ix_posts_moderation_status", "posts", ["moderation_status"])

    # Same drift on comments: the Comment model defines moderation_status but
    # migration 001 never created it. The Post.comments selectin relationship
    # loads it, so any Post read that pulls comments fails without this.
    comment_cols = {col["name"] for col in inspector.get_columns("comments")}
    if "moderation_status" not in comment_cols:
        op.add_column(
            "comments",
            sa.Column("moderation_status", sa.String(20), nullable=False, server_default="approved"),
        )
    comment_indexes = {idx["name"] for idx in inspector.get_indexes("comments")}
    if "ix_comments_moderation_status" not in comment_indexes:
        op.create_index("ix_comments_moderation_status", "comments", ["moderation_status"])


def downgrade() -> None:
    op.drop_index("ix_comments_moderation_status", table_name="comments")
    op.drop_column("comments", "moderation_status")
    op.drop_index("ix_posts_moderation_status", table_name="posts")
    for name, _column in reversed(_COLUMNS):
        op.drop_column("posts", name)
