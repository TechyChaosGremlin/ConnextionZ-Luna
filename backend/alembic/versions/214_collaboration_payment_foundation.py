"""Add provider-neutral collaboration payment state.

Revision ID: 214
Revises: 213
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "214"
down_revision = "213"
branch_labels = None
depends_on = None

_PAYMENT_STATES = (
    "PENDING",
    "AUTHORIZED_HELD",
    "COLLABORATION_ACTIVE",
    "COMPLETED",
    "RELEASE_PENDING",
    "RELEASED",
    "CANCELLED",
    "REFUNDED",
    "DISPUTED",
)


def upgrade() -> None:
    payment_state = postgresql.ENUM(
        *_PAYMENT_STATES,
        name="collaboration_payment_state",
        create_type=False,
    )
    bind = op.get_bind()
    payment_state.create(bind, checkfirst=True)
    existing_payment_states = bind.execute(
        sa.text(
            "SELECT e.enumlabel "
            "FROM pg_type AS t "
            "JOIN pg_namespace AS n ON n.oid = t.typnamespace "
            "JOIN pg_enum AS e ON e.enumtypid = t.oid "
            "WHERE t.typname = :type_name AND n.nspname = current_schema() "
            "ORDER BY e.enumsortorder"
        ),
        {"type_name": "collaboration_payment_state"},
    ).scalars().all()
    if existing_payment_states != list(_PAYMENT_STATES):
        raise RuntimeError(
            "collaboration_payment_state does not match the expected payment states"
        )

    op.create_table(
        "collaboration_payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("collaboration_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payer_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_minor_units", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "state",
            payment_state,
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("payment_provider", sa.String(length=64), nullable=True),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "collaboration_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_pending_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disputed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_minor_units > 0",
            name="ck_collaboration_payments_positive_amount",
        ),
        sa.CheckConstraint(
            "length(currency) = 3 AND currency = upper(currency)",
            name="ck_collaboration_payments_currency",
        ),
        sa.CheckConstraint(
            "payer_user_id <> recipient_user_id",
            name="ck_collaboration_payments_distinct_parties",
        ),
        sa.CheckConstraint(
            "(payment_provider IS NULL AND provider_reference IS NULL) OR "
            "(payment_provider IS NOT NULL AND provider_reference IS NOT NULL)",
            name="ck_collaboration_payments_provider_reference_pair",
        ),
        sa.ForeignKeyConstraint(
            ["collaboration_id"],
            ["collaborations.id"],
            name="fk_collaboration_payments_collaboration_id_collaborations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payer_user_id"],
            ["users.id"],
            name="fk_collaboration_payments_payer_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name="fk_collaboration_payments_recipient_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "collaboration_id",
            name="uq_collaboration_payments_collaboration",
        ),
        sa.UniqueConstraint(
            "payment_provider",
            "provider_reference",
            name="uq_collaboration_payments_provider_reference",
        ),
    )
    op.create_index(
        "ix_collaboration_payments_payer_user_id",
        "collaboration_payments",
        ["payer_user_id"],
    )
    op.create_index(
        "ix_collaboration_payments_recipient_user_id",
        "collaboration_payments",
        ["recipient_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_collaboration_payments_recipient_user_id",
        table_name="collaboration_payments",
    )
    op.drop_index(
        "ix_collaboration_payments_payer_user_id",
        table_name="collaboration_payments",
    )
    op.drop_table("collaboration_payments")
    payment_state = postgresql.ENUM(
        *_PAYMENT_STATES,
        name="collaboration_payment_state",
        create_type=False,
    )
    payment_state.drop(op.get_bind(), checkfirst=True)
