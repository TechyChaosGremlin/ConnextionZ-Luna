from __future__ import annotations

import ast
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

import api.graphql as graphql
from api.graphql import AppContext, ProfileDetailType, schema
from app.models.category import Category
from app.models.user import Profile, User
from repositories.category_repository import CategoryRepository


CANONICAL_NAMES = [
    "Music",
    "Fitness",
    "Travel",
    "Cooking",
    "Art",
    "Tech",
    "Gaming",
    "Fashion",
    "Business",
]


class InMemoryProfileRepository:
    def __init__(self):
        self.profiles: dict[uuid.UUID, Profile] = {}

    async def get_by_user_id(self, user_id):
        return self.profiles.get(user_id)

    async def create(self, profile):
        profile.id = uuid.uuid4()
        profile.categories = []
        profile.onboarding_collab_types = []
        profile.response_time = "< 4 hours"
        profile.open_to_collab = True
        self.profiles[profile.user_id] = profile


class InMemoryCategoryRepository:
    def __init__(self, categories):
        self.categories = categories
        self.requested_slugs = []

    async def get_by_slugs(self, slugs):
        self.requested_slugs.append(list(slugs))
        return [category for category in self.categories if category.slug in slugs]


class TransactionSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.flush = AsyncMock()
        self.nested_started = 0

    def begin_nested(self):
        @asynccontextmanager
        async def nested():
            self.nested_started += 1
            yield self

        return nested()


def make_categories():
    return [
        Category(id=uuid.uuid4(), name=name, slug=name.lower())
        for name in CANONICAL_NAMES
    ]


def update_categories_query():
    return """
        mutation UpdateCategories($input: UpdateMyOnboardingCategoriesInput!) {
          updateMyOnboardingCategories(input: $input) { id name slug }
        }
    """


@pytest.mark.asyncio
async def test_category_repository_reads_slugs_by_profile_user_id():
    user_id = uuid.uuid4()
    result = MagicMock()
    result.scalars.return_value.all.return_value = ["art", "music"]
    db = AsyncMock()
    db.execute.return_value = result

    slugs = await CategoryRepository(db).get_slugs_by_user_id(user_id)

    statement = db.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert slugs == ["art", "music"]
    assert "profile_categories" in sql
    assert "profiles.user_id" in sql
    assert "profiles.deleted_at IS NULL" in sql
    assert "users.deleted_at IS NULL" in sql
    assert user_id in compiled.params.values()


@pytest.mark.asyncio
async def test_migration_seeds_exact_canonical_onboarding_categories():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "212_profile_categories.py"
    )
    module = ast.parse(migration_path.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_CATEGORIES" for target in node.targets)
    )
    rows = ast.literal_eval(assignment.value)
    assert [name for _identity, name, _slug in rows] == CANONICAL_NAMES
    assert [slug for _identity, _name, slug in rows] == [name.lower() for name in CANONICAL_NAMES]
    assert len({identity for identity, _name, _slug in rows}) == len(CANONICAL_NAMES)


def test_category_schema_enforces_unique_names_slugs_relationships_and_cascades():
    category_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in Category.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("name",) in category_uniques
    assert ("slug",) in category_uniques

    association = Profile.categories.property.secondary
    assert association is not None
    assert {column.name for column in association.primary_key.columns} == {
        "profile_id",
        "category_id",
    }
    foreign_keys = {
        (foreign_key.parent.name, foreign_key.ondelete)
        for foreign_key in association.foreign_keys
    }
    assert foreign_keys == {("profile_id", "CASCADE"), ("category_id", "CASCADE")}
    assert User.profile.property.passive_deletes == "all"
    assert User.sessions.property.passive_deletes == "all"
    assert User.stream_sessions.property.passive_deletes == "all"
    assert User.connected_stream_accounts.property.passive_deletes == "all"


@pytest.mark.asyncio
async def test_categories_are_authenticated_and_replace_current_selection(monkeypatch):
    viewer = SimpleNamespace(id=uuid.uuid4(), username="viewer")
    profiles = InMemoryProfileRepository()
    categories = make_categories()
    category_repository = InMemoryCategoryRepository(categories)
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: profiles,
    )
    monkeypatch.setattr(
        "repositories.category_repository.CategoryRepository",
        lambda _db: category_repository,
    )
    db = TransactionSession()
    context = AppContext(db, viewer)
    monkeypatch.setattr(
        graphql,
        "_profile_detail_for_user",
        AsyncMock(
            side_effect=lambda _ctx, user: ProfileDetailType(
                id=profiles.profiles[user.id].id,
                username=user.username,
                display_name=user.username,
            )
        ),
    )

    first_slugs = ["music", "fitness", "travel"]
    first = await schema.execute(
        update_categories_query(),
        variable_values={"input": {"slugs": first_slugs}},
        context_value=context,
    )
    assert first.errors is None
    expected_first = [
        {"id": str(category.id), "name": category.name, "slug": category.slug}
        for category in categories[:3]
    ]
    assert first.data == {"updateMyOnboardingCategories": expected_first}
    stored = profiles.profiles[viewer.id]
    assert stored.user_id == viewer.id
    assert [category.slug for category in stored.categories] == first_slugs
    assert db.nested_started == 1
    assert db.commit.await_count == 1

    second_slugs = ["art", "business"]
    second = await schema.execute(
        update_categories_query(),
        variable_values={"input": {"slugs": second_slugs}},
        context_value=context,
    )
    assert second.errors is None
    assert [category.slug for category in stored.categories] == second_slugs
    assert db.nested_started == 2
    assert db.commit.await_count == 2

    query = await schema.execute(
        """
        query MyCategories {
          me {
            onboardingPreferences {
              collabTypes
              responseTime
              openToCollab
              categories { id name slug }
            }
          }
        }
        """,
        context_value=context,
    )
    assert query.errors is None
    assert query.data == {
        "me": {
            "onboardingPreferences": {
                "collabTypes": [],
                "responseTime": "< 4 hours",
                "openToCollab": True,
                "categories": [
                    {"id": str(categories[4].id), "name": "Art", "slug": "art"},
                    {
                        "id": str(categories[8].id),
                        "name": "Business",
                        "slug": "business",
                    },
                ],
            }
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slugs",
    [
        ["music", "music"],
        ["not-a-canonical-category"],
        [],
    ],
)
async def test_category_update_rejects_duplicates_unknown_and_empty(monkeypatch, slugs):
    viewer = SimpleNamespace(id=uuid.uuid4(), username="viewer")
    profiles = InMemoryProfileRepository()
    category_repository = InMemoryCategoryRepository(make_categories())
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: profiles,
    )
    monkeypatch.setattr(
        "repositories.category_repository.CategoryRepository",
        lambda _db: category_repository,
    )
    db = TransactionSession()
    result = await schema.execute(
        update_categories_query(),
        variable_values={"input": {"slugs": slugs}},
        context_value=AppContext(db, viewer),
    )

    assert result.data is None
    assert result.errors
    assert not profiles.profiles
    db.commit.assert_not_awaited()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_category_operations_require_authentication(monkeypatch):
    repository = InMemoryCategoryRepository(make_categories())
    monkeypatch.setattr(
        "repositories.category_repository.CategoryRepository",
        lambda _db: repository,
    )
    context = AppContext(TransactionSession())

    mutation = await schema.execute(
        update_categories_query(),
        variable_values={"input": {"slugs": ["music"]}},
        context_value=context,
    )
    query = await schema.execute(
        "{ me { onboardingPreferences { categories { slug } } } }",
        context_value=context,
    )

    assert mutation.errors and "Authentication required" in mutation.errors[0].message
    assert query.errors and "Authentication required" in query.errors[0].message
    assert repository.requested_slugs == []


@pytest.mark.asyncio
async def test_user_category_relationships_are_isolated(monkeypatch):
    user_a = SimpleNamespace(id=uuid.uuid4(), username="user-a")
    user_b = SimpleNamespace(id=uuid.uuid4(), username="user-b")
    profiles = InMemoryProfileRepository()
    categories = make_categories()
    repository = InMemoryCategoryRepository(categories)
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: profiles,
    )
    monkeypatch.setattr(
        "repositories.category_repository.CategoryRepository",
        lambda _db: repository,
    )
    db = TransactionSession()
    for user, slugs in ((user_a, ["music"]), (user_b, ["business", "art"])):
        result = await schema.execute(
            update_categories_query(),
            variable_values={"input": {"slugs": slugs}},
            context_value=AppContext(db, user),
        )
        assert result.errors is None

    assert [item.slug for item in profiles.profiles[user_a.id].categories] == ["music"]
    assert [item.slug for item in profiles.profiles[user_b.id].categories] == ["business", "art"]
    assert "user_id" not in schema._schema.get_type("UpdateMyOnboardingCategoriesInput").fields


@pytest.mark.asyncio
async def test_category_relationship_flush_failure_does_not_commit(monkeypatch):
    viewer = SimpleNamespace(id=uuid.uuid4(), username="viewer")
    profiles = InMemoryProfileRepository()
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: profiles,
    )
    monkeypatch.setattr(
        "repositories.category_repository.CategoryRepository",
        lambda _db: InMemoryCategoryRepository(make_categories()),
    )
    db = TransactionSession()
    db.flush.side_effect = RuntimeError("association insert failed")

    result = await schema.execute(
        update_categories_query(),
        variable_values={"input": {"slugs": ["music"]}},
        context_value=AppContext(db, viewer),
    )

    assert result.data is None
    assert result.errors
    assert db.nested_started == 1
    db.commit.assert_not_awaited()
