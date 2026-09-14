"""Alembic migration script template.

Generates revision ID, creates/upgrades/downgrades tables.
"""

revision = "5992447b5c45"
down_revision = ('002_pgvector', '007')
branch_labels = None
depends_on = None


from alembic import op
import sqlalchemy as sa



def upgrade() -> None:
    pass


def downgrade() -> None:
    pass