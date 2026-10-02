"""Focused creator-discovery to collaboration-invite handoff tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.graphql import AppContext, _create_collaboration, _discover_creators, schema
from app.models.user import AccountStatus, User, UserRole
from services.collaboration_service import CollaborationInviteEligibilityService



def make_user(username: str, *, status=AccountStatus.ACTIVE, deleted_at=None) -> User:
    now = datetime.now(timezone.utc)
    return User(
        id=uuid.uuid4(), email=f"{username}@example.test", username=username,
        hashed_password="hashed", role=UserRole.CREATOR, status=status,
        email_verified=True, mfa_enabled=False, created_at=now, updated_at=now,
        deleted_at=deleted_at,
    )


def make_profile(user: User, **overrides):
    now = datetime.now(timezone.utc)
    values = dict(
        id=uuid.uuid4(), user_id=user.id, display_name=user.username, bio="creator",
        avatar_url=None, cover_image_url=None, website_url=None, location=None,
        social_links=None, tags=["music"], follower_count=0, following_count=0,
        collaboration_count=0, total_likes=0, open_to_collab=True,
        private_account=False, deleted_at=None, created_at=now, updated_at=now,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def make_input(participant_ids):
    return SimpleNamespace(
        title="Discovery handoff", description=None, content_type="video", platform="youtube",
        tags=["music"], participant_ids=participant_ids, budget_min=None,
        budget_max=None, budget_currency="USD",
    )


@pytest.fixture
def discovery_dependencies(monkeypatch):
    viewer = make_user("viewer")
    creators = [
        make_user("first_creator"),
        make_user("second_creator"),
    ]
    profiles = [
        make_profile(creators[0], tags=["music"]),
        make_profile(creators[1], tags=["art"]),
    ]
    users = {viewer.id: viewer, **{creator.id: creator for creator in creators}}
    profiles_by_user_id = {
        viewer.id: make_profile(viewer, tags=["music"]),
        **{profile.user_id: profile for profile in profiles},
    }

    async def get_all(self, skip=0, limit=100, **filters):
        return profiles[skip:skip + limit]

    async def get_profile(self, user_id):
        return profiles_by_user_id.get(user_id)

    async def get_user(self, user_id):
        return users.get(user_id)

    async def get_hidden_creator_ids(self, viewer_id, candidate_ids):
        return set()

    async def get_restricted_ids(self, initiator_id, target_ids):
        return set()

    async def is_following(self, follower_id, following_id):
        return False

    async def are_following_each_other(self, follower_id, following_id):
        return False, False

    async def are_following_each_other_for_users(self, viewer_id, creator_ids):
        return {creator_id: (False, False) for creator_id in creator_ids}

    async def creator_affinity(self, user_id, limit=20):
        return []

    async def get_pairwise_history(self, initiator_id, target_id):
        return []

    async def get_pairwise_history_for_users(self, initiator_id, target_ids):
        return {target_id: [] for target_id in target_ids}

    async def get_reputation_score(self, user_id):
        return None

    async def get_reputation_scores(self, user_ids):
        return {}

    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_all", get_all)
    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_by_user_id", get_profile)
    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_id", get_user)
    monkeypatch.setattr("repositories.social_repository.FeedSafetyRepository.get_hidden_creator_ids", get_hidden_creator_ids)
    monkeypatch.setattr("repositories.social_repository.FeedSafetyRepository.get_invitation_restricted_user_ids", get_restricted_ids)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.is_following", is_following)
    monkeypatch.setattr("repositories.follow_repository.FollowRepository.are_following_each_other", are_following_each_other)
    monkeypatch.setattr("repositories.follow_repository.FollowRepository.are_following_each_other_for_users", are_following_each_other_for_users)
    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.creator_affinity", creator_affinity)
    monkeypatch.setattr("repositories.collaboration_repository.CollaborationRepository.get_pairwise_history", get_pairwise_history)
    monkeypatch.setattr("repositories.collaboration_repository.CollaborationRepository.get_pairwise_history_for_users", get_pairwise_history_for_users)
    monkeypatch.setattr("repositories.reputation_repository.ReputationRepository.get_reputation_score", get_reputation_score)
    monkeypatch.setattr("repositories.reputation_repository.ReputationRepository.get_reputation_scores", get_reputation_scores)

    activity_result = MagicMock()
    activity_result.all.return_value = []
    db = AsyncMock()
    db.execute.return_value = activity_result

    return viewer, creators, profiles, db


async def discover_with_dependencies(viewer, db, *, first=20, after=None):
    return await _discover_creators(
        AppContext(db=db, current_user=viewer),
        None,
        None,
        first,
        after,
    )


@pytest.mark.asyncio
async def test_discover_creators_graphql_returns_filtered_creator(discovery_dependencies):
        viewer, creators, profiles, db = discovery_dependencies

        result = await schema.execute(
                """
                query DiscoverCreators($tags: [String!]) {
                    discoverCreators(tags: $tags) {
                        totalCount
                        edges {
                            node {
                                user { id username }
                                profile { id displayName tags }
                                matchingTags
                                relevanceScore
                                reputationScore
                            }
                        }
                    }
                }
                """,
                variable_values={"tags": ["music"]},
                context_value=AppContext(db=db, current_user=viewer),
        )

        assert result.errors is None
        assert result.data is not None
        discovery = result.data["discoverCreators"]
        assert discovery["totalCount"] == 1
        assert len(discovery["edges"]) == 1
        card = discovery["edges"][0]["node"]
        assert card["user"] == {"id": str(creators[0].id), "username": creators[0].username}
        assert card["profile"] == {
            "id": str(profiles[0].id),
            "displayName": profiles[0].display_name,
            "tags": ["music"],
        }
        assert card["matchingTags"] == ["music"]
        assert card["relevanceScore"] > 0
        assert card["reputationScore"] is None


@pytest.fixture
def eligibility_dependencies(monkeypatch):
    initiator = make_user("initiator")
    target = make_user("target")
    state = {"target": target, "profile": make_profile(target), "restricted": set(), "follows": False}

    async def get_user(self, user_id):
        return state["target"] if user_id == state["target"].id else None

    async def get_profile(self, user_id):
        return state["profile"] if user_id == state["target"].id else None

    async def restricted_ids(self, initiator_id, target_ids):
        assert initiator_id == initiator.id
        return state["restricted"]

    async def is_following(self, follower_id, following_id):
        return state["follows"]

    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_id", get_user)
    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_by_user_id", get_profile)
    monkeypatch.setattr("repositories.social_repository.FeedSafetyRepository.get_invitation_restricted_user_ids", restricted_ids)
    monkeypatch.setattr("repositories.social_repository.FollowRepository.is_following", is_following)
    return initiator, state


@pytest.mark.asyncio
async def test_discover_creators_returns_declared_creator_card(
    monkeypatch,
    eligibility_dependencies,
):
    initiator, state = eligibility_dependencies

    async def get_all(self, skip=0, limit=100, **filters):
        return [state["profile"]]

    monkeypatch.setattr(
        "repositories.profile_repository.ProfileRepository.get_all",
        get_all,
    )

    db = AsyncMock()

    # Activity query
    activity_result = MagicMock()
    activity_result.all.return_value = []
    
    # Safety queries: blocked, blocking, muted
    empty_result_1 = MagicMock()
    empty_result_1.scalars.return_value = []

    empty_result_2 = MagicMock()
    empty_result_2.scalars.return_value = []

    empty_result_3 = MagicMock()
    empty_result_3.scalars.return_value = []

    empty_result_4 = MagicMock()
    empty_result_4.scalars.return_value = []

    empty_result_5 = MagicMock()
    empty_result_5.scalars.return_value.all.return_value = []

    empty_result_6 = MagicMock()
    empty_result_6.scalar_one_or_none.return_value = None

    empty_batch_result = MagicMock()
    empty_batch_result.all.return_value = []
    empty_batch_result.scalars.return_value.all.return_value = []

    db.execute.side_effect = [
        activity_result,
        empty_result_1,
        empty_result_2,
        empty_result_3,
        empty_result_4,
        empty_result_5,
        empty_result_6,
        empty_batch_result,
        empty_batch_result,
        empty_batch_result,
    ]

    result = await _discover_creators(
        AppContext(db=db, current_user=initiator),
        None,
        ["music"],
        20,
        None,
    )

    assert result is not None
    assert len(result.edges) == 1
    assert result.edges[0].node.user.id == state["target"].id


@pytest.mark.asyncio
async def test_discover_creators_after_cursor_returns_next_creator(discovery_dependencies):
    viewer, creators, _, db = discovery_dependencies

    first_page = await discover_with_dependencies(viewer, db, first=1)
    second_page = await discover_with_dependencies(viewer, db, first=1, after="1")

    assert [edge.node.user.id for edge in first_page.edges] == [creators[0].id]
    assert [edge.node.user.id for edge in second_page.edges] == [creators[1].id]


@pytest.mark.asyncio
async def test_discover_creators_filters_scores_ranks_and_paginates_cards(
    monkeypatch, discovery_dependencies,
):
    viewer, creators, profiles, db = discovery_dependencies
    next_creator = make_user("next_creator")
    excluded_by_tags = make_user("excluded_by_tags")
    excluded_by_query = make_user("excluded_by_query")
    users = {user.id: user for user in [*creators, next_creator, excluded_by_tags, excluded_by_query]}

    for profile in profiles:
        profile.display_name = "Studio creator"
        profile.tags = ["music"]
    profiles.extend([
        make_profile(next_creator, display_name="Studio next"),
        make_profile(excluded_by_tags, display_name="Studio art", tags=["art"]),
        make_profile(excluded_by_query, display_name="Unrelated creator"),
    ])

    from repositories.profile_repository import ProfileRepository

    original_get_profile = ProfileRepository.get_by_user_id

    async def get_profile(self, user_id):
        if user_id == next_creator.id:
            return profiles[2]
        return await original_get_profile(self, user_id)

    async def get_user(self, user_id):
        return users.get(user_id)

    async def creator_affinity(self, user_id, limit=20):
        assert user_id == viewer.id
        return [(creators[0].id, 20.0), (creators[1].id, 100.0)]

    monkeypatch.setattr(ProfileRepository, "get_by_user_id", get_profile)
    monkeypatch.setattr("repositories.user_repository.UserRepository.get_by_id", get_user)
    monkeypatch.setattr("repositories.analytics_repository.AnalyticsRepository.creator_affinity", creator_affinity)

    async def request(after=None):
        return await _discover_creators(
            AppContext(db=db, current_user=viewer), "studio", ["music"], 2, after,
        )

    first_page = await request()
    repeat = await request()
    second_page = await request(first_page.page_info.end_cursor)

    assert [edge.node.user.id for edge in first_page.edges] == [creators[1].id, creators[0].id]
    assert first_page.edges[0].node.relevance_score > first_page.edges[1].node.relevance_score
    assert [edge.node.user.id for edge in repeat.edges] == [creators[1].id, creators[0].id]
    assert first_page.total_count == 2
    assert first_page.page_info.has_next_page is True
    assert [edge.node.user.id for edge in second_page.edges] == [next_creator.id]
    assert second_page.total_count == 1
    assert second_page.page_info.has_next_page is False
    for edge in [*first_page.edges, *second_page.edges]:
        assert edge.node.profile.id in {profile.id for profile in profiles[:3]}
        assert edge.node.matching_tags == ["music"]
        assert edge.node.relevance_score is not None


@pytest.mark.asyncio
async def test_discover_creators_orders_results_by_relevance_score(discovery_dependencies):
    viewer, creators, profiles, db = discovery_dependencies

    result = await discover_with_dependencies(viewer, db)
    scores = [edge.node.relevance_score for edge in result.edges]

    assert [edge.node.user.id for edge in result.edges] == [creators[0].id, creators[1].id]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > scores[1]
    assert profiles[0].tags == ["music"]


@pytest.mark.asyncio
async def test_discover_creators_batches_scoring_lookups(monkeypatch, discovery_dependencies):
    viewer, creators, _, db = discovery_dependencies
    calls = {"follows": [], "history": [], "reputation": []}

    async def follow_states(self, viewer_id, creator_ids):
        calls["follows"].append((viewer_id, creator_ids))
        return {creator_id: (False, False) for creator_id in creator_ids}

    async def collaboration_histories(self, viewer_id, creator_ids):
        calls["history"].append((viewer_id, creator_ids))
        return {creator_id: [] for creator_id in creator_ids}

    async def reputation_scores(self, user_ids):
        calls["reputation"].append(user_ids)
        return {}

    async def single_lookup_must_not_run(*args, **kwargs):
        raise AssertionError("single-candidate scoring lookup was called")

    monkeypatch.setattr(
        "repositories.follow_repository.FollowRepository.are_following_each_other_for_users",
        follow_states,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_pairwise_history_for_users",
        collaboration_histories,
    )
    monkeypatch.setattr(
        "repositories.reputation_repository.ReputationRepository.get_reputation_scores",
        reputation_scores,
    )
    monkeypatch.setattr(
        "repositories.follow_repository.FollowRepository.are_following_each_other",
        single_lookup_must_not_run,
    )
    monkeypatch.setattr(
        "repositories.collaboration_repository.CollaborationRepository.get_pairwise_history",
        single_lookup_must_not_run,
    )
    monkeypatch.setattr(
        "repositories.reputation_repository.ReputationRepository.get_reputation_score",
        single_lookup_must_not_run,
    )

    result = await discover_with_dependencies(viewer, db)
    expected_ids = [creator.id for creator in creators]

    assert len(result.edges) == len(creators)
    assert calls["follows"] == [(viewer.id, expected_ids)]
    assert calls["history"] == [(viewer.id, expected_ids)]
    assert calls["reputation"] == [expected_ids]


@pytest.mark.asyncio
async def test_discover_creators_reuses_users_fetched_during_eligibility(
    monkeypatch,
    discovery_dependencies,
):
    viewer, creators, _, db = discovery_dependencies
    user_lookups = []
    users = {creator.id: creator for creator in creators}

    async def tracked_get_user(self, user_id):
        user_lookups.append(user_id)
        return users.get(user_id)

    monkeypatch.setattr(
        "repositories.user_repository.UserRepository.get_by_id",
        tracked_get_user,
    )

    result = await discover_with_dependencies(viewer, db)

    assert len(result.edges) == len(creators)
    assert user_lookups == [creator.id for creator in creators]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_data",
    ["affinity", "reputation", "collaboration_history", "viewer_profile", "tags"],
)
async def test_discover_creators_handles_missing_scoring_data(
    monkeypatch,
    discovery_dependencies,
    missing_data,
):
    viewer, creators, profiles, db = discovery_dependencies

    if missing_data == "affinity":
        async def missing_affinity(self, user_id, limit=20):
            return None

        monkeypatch.setattr(
            "repositories.analytics_repository.AnalyticsRepository.creator_affinity",
            missing_affinity,
        )
    elif missing_data == "reputation":
        async def missing_reputation(self, user_ids):
            return {}

        monkeypatch.setattr(
            "repositories.reputation_repository.ReputationRepository.get_reputation_scores",
            missing_reputation,
        )
    elif missing_data == "collaboration_history":
        async def missing_history(self, initiator_id, target_ids):
            return {}

        monkeypatch.setattr(
            "repositories.collaboration_repository.CollaborationRepository.get_pairwise_history_for_users",
            missing_history,
        )
    elif missing_data == "viewer_profile":
        async def missing_viewer_profile(self, user_id):
            if user_id == viewer.id:
                return None
            return next(
                (profile for profile in profiles if profile.user_id == user_id),
                None,
            )

        monkeypatch.setattr(
            "repositories.profile_repository.ProfileRepository.get_by_user_id",
            missing_viewer_profile,
        )
    else:
        profiles[0].tags = None

    result = await discover_with_dependencies(viewer, db)

    assert {edge.node.user.id for edge in result.edges} == {creator.id for creator in creators}
    assert all(edge.node.relevance_score is not None for edge in result.edges)
    if missing_data == "tags":
        missing_tags_edge = next(
            edge for edge in result.edges if edge.node.user.id == creators[0].id
        )
        assert missing_tags_edge.node.matching_tags is None


@pytest.mark.asyncio
async def test_discover_creators_tie_breaks_equal_relevance_score_by_profile_id(discovery_dependencies):
    viewer, creators, profiles, db = discovery_dependencies
    profiles[1].tags = ["music"]  # match profiles[0] so relevance_score ties

    result = await discover_with_dependencies(viewer, db)
    scores = [edge.node.relevance_score for edge in result.edges]
    assert scores[0] == scores[1]

    expected_order = [profile.id for profile in sorted(profiles, key=lambda profile: str(profile.id))]
    assert [edge.node.profile.id for edge in result.edges] == expected_order

    repeat = await discover_with_dependencies(viewer, db)
    assert [edge.node.profile.id for edge in repeat.edges] == expected_order


@pytest.mark.asyncio
async def test_discover_creators_excludes_creator_not_open_to_collab(discovery_dependencies):
    viewer, creators, profiles, db = discovery_dependencies
    profiles[0].open_to_collab = False

    result = await discover_with_dependencies(viewer, db)

    assert [edge.node.user.id for edge in result.edges] == [creators[1].id]


@pytest.mark.asyncio
@pytest.mark.parametrize("account_change", ["inactive", "deleted"])
async def test_discover_creators_excludes_inactive_or_deleted_creator(
    discovery_dependencies,
    account_change,
):
    viewer, creators, _, db = discovery_dependencies
    if account_change == "inactive":
        creators[0].status = AccountStatus.SUSPENDED
    else:
        creators[0].deleted_at = "2026-01-01"

    result = await discover_with_dependencies(viewer, db)

    assert [edge.node.user.id for edge in result.edges] == [creators[1].id]


@pytest.mark.asyncio
async def test_discover_creators_excludes_private_unfollowed_creator(discovery_dependencies):
    viewer, creators, profiles, db = discovery_dependencies
    profiles[0].private_account = True

    result = await discover_with_dependencies(viewer, db)

    assert [edge.node.user.id for edge in result.edges] == [creators[1].id]

@pytest.mark.asyncio
async def test_discovered_creator_id_becomes_pending_collaboration_participant(monkeypatch, eligibility_dependencies):
    initiator, state = eligibility_dependencies
    participants = []

    async def get_all(self, skip=0, limit=100, **filters):
        return [state["profile"]]

    async def create(self, collaboration):
        collaboration.id = uuid.uuid4()
        return collaboration

    async def add_participant(self, participant):
        participants.append(participant)
        return participant

    async def track_event(self, **kwargs):
        return None

    monkeypatch.setattr("repositories.profile_repository.ProfileRepository.get_all", get_all)
    monkeypatch.setattr("repositories.collaboration_repository.CollaborationRepository.create", create)
    monkeypatch.setattr("repositories.collaboration_repository.CollaborationRepository.add_participant", add_participant)
    monkeypatch.setattr("services.analytics_event_service.AnalyticsEventService.track_event", track_event)

    db = AsyncMock()

    activity_result = MagicMock()
    activity_result.all.return_value = []

    empty_result_1 = MagicMock()
    empty_result_1.scalars.return_value = []

    empty_result_2 = MagicMock()
    empty_result_2.scalars.return_value = []

    empty_result_3 = MagicMock()
    empty_result_3.scalars.return_value = []

    empty_result_4 = MagicMock()
    empty_result_4.scalars.return_value = []

    empty_result_5 = MagicMock()
    empty_result_5.scalars.return_value.all.return_value = []

    empty_result_6 = MagicMock()
    empty_result_6.scalar_one_or_none.return_value = None

    empty_batch_result = MagicMock()
    empty_batch_result.all.return_value = []
    empty_batch_result.scalars.return_value.all.return_value = []

    db.execute.side_effect = [
        activity_result,
        empty_result_1,
        empty_result_2,
        empty_result_3,
        empty_result_4,
        empty_result_5,
        empty_result_6,
        empty_batch_result,
        empty_batch_result,
        empty_batch_result,
    ]

    ctx = AppContext(db=db, current_user=initiator, session_id="handoff")
    discovery = await _discover_creators(ctx, None, ["music"], 20, None)

    await _create_collaboration(ctx, make_input([discovery.edges[0].node.user.id]))

    invited = next(participant for participant in participants if participant.role == "participant")
    assert invited.user_id == state["target"].id
    assert invited.accepted is not True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_update", "error"),
    [
        ({"target": None}, "not found"),
        ({"target_status": AccountStatus.SUSPENDED}, "not active"),
        ({"target_deleted": "2026-01-01"}, "not found"),
        ({"restricted": "target"}, "block or mute"),
        ({"open_to_collab": False}, "not open"),
        ({"private_account": True}, "followers"),
    ],
)
async def test_invite_eligibility_rejects_ineligible_creators(eligibility_dependencies, state_update, error):
    initiator, state = eligibility_dependencies
    if state_update.get("target") is None and "target" in state_update:
        target_id = uuid.uuid4()
    else:
        target_id = state["target"].id
    if "target_status" in state_update:
        state["target"].status = state_update["target_status"]
    if "target_deleted" in state_update:
        state["target"].deleted_at = state_update["target_deleted"]
    if "restricted" in state_update:
        state["restricted"] = {state["target"].id}
    if "open_to_collab" in state_update:
        state["profile"].open_to_collab = state_update["open_to_collab"]
    if "private_account" in state_update:
        state["profile"].private_account = state_update["private_account"]

    with pytest.raises((ValueError, PermissionError), match=error):
        await CollaborationInviteEligibilityService(AsyncMock()).validate_participant_ids(initiator, [target_id])


@pytest.mark.asyncio
async def test_invite_eligibility_rejects_self_and_duplicate_ids(eligibility_dependencies):
    initiator, state = eligibility_dependencies
    service = CollaborationInviteEligibilityService(AsyncMock())

    with pytest.raises(ValueError, match="yourself"):
        await service.validate_participant_ids(initiator, [initiator.id])
    with pytest.raises(ValueError, match="Duplicate"):
        await service.validate_participant_ids(initiator, [state["target"].id, state["target"].id])