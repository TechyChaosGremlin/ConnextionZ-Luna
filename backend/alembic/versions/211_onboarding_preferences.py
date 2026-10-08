"""Store onboarding collaboration preferences on the user profile.

Revision ID: 211
Revises: 210
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "211"
down_revision = "210"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "onboarding_collab_types",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("profiles", "onboarding_collab_types")
