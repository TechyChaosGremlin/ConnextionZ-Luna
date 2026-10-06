"""Attribute follow signals to their originating stream session.

Revision ID: 203
Revises: 202
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "203"
down_revision = "202"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interaction_signals",
        sa.Column("stream_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_interaction_signals_stream_session_id_stream_sessions",
        "interaction_signals",
        "stream_sessions",
        ["stream_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_interaction_signals_stream_type",
        "interaction_signals",
        ["stream_session_id", "signal_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_interaction_signals_stream_type", table_name="interaction_signals")
    op.drop_constraint(
        "fk_interaction_signals_stream_session_id_stream_sessions",
        "interaction_signals",
        type_="foreignkey",
    )
    op.drop_column("interaction_signals", "stream_session_id")
