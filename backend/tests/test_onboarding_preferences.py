from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import api.graphql as graphql
from api.graphql import AppContext, ProfileDetailType, schema
from app.models.user import Profile


class InMemoryProfileRepository:
    def __init__(self):
        self.profiles: dict[uuid.UUID, Profile] = {}

    async def get_by_user_id(self, user_id):
        return self.profiles.get(user_id)

    async def create(self, profile):
        profile.id = uuid.uuid4()
        profile.onboarding_collab_types = []
        profile.response_time = "< 4 hours"
        profile.open_to_collab = True
        self.profiles[profile.user_id] = profile

    async def update(self, profile):
        self.profiles[profile.user_id] = profile
        return profile


def preferences_mutation() -> str:
    return """
        mutation Update($input: UpdateOnboardingPreferencesInput!) {
          updateMyOnboardingPreferences(input: $input) {
            collabTypes responseTime openToCollab
          }
        }
    """


@pytest.mark.asyncio
async def test_onboarding_preferences_create_update_and_read_for_authenticated_user(monkeypatch):
    viewer = SimpleNamespace(id=uuid.uuid4(), username="viewer")
    repository = InMemoryProfileRepository()
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: repository,
    )
    db = SimpleNamespace(commit=AsyncMock())
    context = AppContext(db, viewer)
    monkeypatch.setattr(
        graphql,
        "_profile_detail_for_user",
        AsyncMock(
            side_effect=lambda _ctx, user: ProfileDetailType(
                id=repository.profiles[user.id].id,
                username=user.username,
                display_name=user.username,
            )
        ),
    )

    mutation = await schema.execute(
        preferences_mutation(),
        variable_values={
            "input": {
                "collabTypes": ["Duet / Remix", "Brand Deal"],
                "responseTime": "< 1 hour",
                "openToCollab": False,
            }
        },
        context_value=context,
    )

    assert mutation.errors is None
    expected = {
        "collabTypes": ["Duet / Remix", "Brand Deal"],
        "responseTime": "< 1 hour",
        "openToCollab": False,
    }
    assert mutation.data == {"updateMyOnboardingPreferences": expected}
    profile = repository.profiles[viewer.id]
    assert profile.user_id == viewer.id
    assert profile.onboarding_collab_types == expected["collabTypes"]
    assert profile.response_time == expected["responseTime"]
    assert profile.open_to_collab is False

    updated = {
        "collabTypes": ["Podcast / Interview"],
        "responseTime": "< 24 hours",
        "openToCollab": True,
    }
    update = await schema.execute(
        preferences_mutation(),
        variable_values={"input": updated},
        context_value=context,
    )
    assert update.errors is None
    assert update.data == {"updateMyOnboardingPreferences": updated}

    other_user = SimpleNamespace(id=uuid.uuid4(), username="other")
    other_preferences = {
        "collabTypes": ["Brand Deal"],
        "responseTime": "< 4 hours",
        "openToCollab": False,
    }
    other_update = await schema.execute(
        preferences_mutation(),
        variable_values={"input": other_preferences},
        context_value=AppContext(db, other_user),
    )
    assert other_update.errors is None
    assert other_update.data == {"updateMyOnboardingPreferences": other_preferences}
    assert repository.profiles[viewer.id].onboarding_collab_types == updated["collabTypes"]
    assert repository.profiles[viewer.id].response_time == updated["responseTime"]

    query = await schema.execute(
        """
        query MyPreferences {
          me {
            onboardingPreferences { collabTypes responseTime openToCollab }
          }
        }
        """,
        context_value=context,
    )
    assert query.errors is None
    assert query.data == {"me": {"onboardingPreferences": updated}}
    assert db.commit.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("collab_types", "response_time"),
    [
        ([], "< 4 hours"),
        (["Not an onboarding option"], "< 4 hours"),
        (["Duet / Remix", "Duet / Remix"], "< 4 hours"),
        (["Duet / Remix"], "2-3 days"),
    ],
)
async def test_onboarding_preferences_reject_values_outside_existing_options(
    monkeypatch, collab_types, response_time
):
    viewer = SimpleNamespace(id=uuid.uuid4(), username="viewer")
    repository = InMemoryProfileRepository()
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: repository,
    )
    db = SimpleNamespace(commit=AsyncMock())

    result = await schema.execute(
        preferences_mutation(),
        variable_values={
            "input": {
                "collabTypes": collab_types,
                "responseTime": response_time,
                "openToCollab": True,
            }
        },
        context_value=AppContext(db, viewer),
    )

    assert result.data is None
    assert result.errors
    assert not repository.profiles
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_onboarding_preferences_require_authentication(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: InMemoryProfileRepository(),
    )
    mutation = await schema.execute(
        preferences_mutation(),
        variable_values={
            "input": {
                "collabTypes": ["Paid Collaboration"],
                "responseTime": "< 4 hours",
                "openToCollab": True,
            }
        },
        context_value=AppContext(db),
    )
    query = await schema.execute(
        "{ me { onboardingPreferences { collabTypes } } }",
        context_value=AppContext(db),
    )

    assert mutation.errors and "Authentication required" in mutation.errors[0].message
    assert query.errors and "Authentication required" in query.errors[0].message


@pytest.mark.asyncio
async def test_onboarding_preferences_cannot_be_read_from_another_profile(monkeypatch):
    owner = SimpleNamespace(id=uuid.uuid4(), username="owner")
    viewer = SimpleNamespace(id=uuid.uuid4(), username="viewer")
    repository = InMemoryProfileRepository()
    await repository.create(Profile(user_id=owner.id, display_name=owner.username))
    profile = repository.profiles[owner.id]
    profile.onboarding_collab_types = ["Brand Deal"]
    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository",
        lambda _db: repository,
    )
    monkeypatch.setattr(
        graphql,
        "_profile",
        AsyncMock(
            return_value=ProfileDetailType(
                id=profile.id,
                username=owner.username,
                display_name=owner.username,
            )
        ),
    )

    result = await schema.execute(
        """
        query OtherProfile {
          profile(username: "owner") {
            onboardingPreferences { collabTypes responseTime openToCollab }
          }
        }
        """,
        context_value=AppContext(SimpleNamespace(), viewer),
    )

    assert result.data == {"profile": None}
    assert result.errors
    assert "only available to the profile owner" in result.errors[0].message
