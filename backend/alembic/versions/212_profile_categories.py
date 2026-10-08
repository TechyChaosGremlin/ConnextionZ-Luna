"""Add canonical onboarding categories and profile selections.

Revision ID: 212
Revises: 211
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "212"
down_revision = "211"
branch_labels = None
depends_on = None


_CATEGORIES = (
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e001", "Music", "music"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e002", "Fitness", "fitness"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e003", "Travel", "travel"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e004", "Cooking", "cooking"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e005", "Art", "art"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e006", "Tech", "tech"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e007", "Gaming", "gaming"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e008", "Fashion", "fashion"),
    ("5ecff183-8bf7-5f9d-8d34-3e5a63b3e009", "Business", "business"),
)


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.CheckConstraint("length(name) > 0", name="ck_categories_name_nonempty"),
        sa.CheckConstraint("length(slug) > 0", name="ck_categories_slug_nonempty"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_categories_name"),
        sa.UniqueConstraint("slug", name="uq_categories_slug"),
    )
    op.create_table(
        "profile_categories",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "category_id"),
    )
    op.create_index(
        "ix_profile_categories_category_id",
        "profile_categories",
        ["category_id"],
        unique=False,
    )

    categories = sa.table(
        "categories",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String(length=64)),
        sa.column("slug", sa.String(length=64)),
    )
    statement = postgresql.insert(categories).values(
        [
            {"id": uuid.UUID(identity), "name": name, "slug": slug}
            for identity, name, slug in _CATEGORIES
        ]
    )
    op.get_bind().execute(statement.on_conflict_do_nothing(index_elements=["slug"]))


def downgrade() -> None:
    op.drop_index("ix_profile_categories_category_id", table_name="profile_categories")
    op.drop_table("profile_categories")
    op.drop_table("categories")
