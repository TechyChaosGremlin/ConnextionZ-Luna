"""Persist token revocations for the PostgreSQL-only beta."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "218"
down_revision = "217"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "token_revocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("jti", name="uq_token_revocations_jti"),
    )
    op.create_index(
        "ix_token_revocations_expires_at", "token_revocations", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_token_revocations_expires_at", table_name="token_revocations")
    op.drop_table("token_revocations")
