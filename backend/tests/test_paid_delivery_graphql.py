"""Focused tests for the gated Paid GraphQL delivery integration."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

import api.graphql as graphql
from api.graphql import AppContext, FeedAlgorithm, FeedPageType, _feed, schema
from app.models.paid_delivery import PaidDelivery
from app.models.user import Profile
from services.paid_campaign_service import (
    PaidCampaignService,
    PaidCandidate,
    PaidCandidatePage,
)
from services.paid_delivery_service import PaidDeliveryService
from tests.test_paid_campaigns import NOW, campaign
from tests.test_paid_campaigns import db as db
from tests.test_paid_campaigns import user_and_post


@pytest.mark.asyncio
async def test_paid_candidates_are_selected_and_serialized_without_accounting(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    first_creator, first_post = await user_and_post(db)
    second_creator, second_post = await user_and_post(db)
    db.add(Profile(user_id=first_creator.id, display_name="First Creator"))
    db.add(Profile(user_id=second_creator.id, display_name="Second Creator"))
    await db.flush()

    candidates = [
        PaidCandidate(post_id=first_post.id, paid_campaign_id=uuid.uuid4()),
        PaidCandidate(post_id=second_post.id, paid_campaign_id=uuid.uuid4()),
    ]
    cursor = "paid1.server-generated-cursor"
    page = PaidCandidatePage(items=candidates, next_cursor=cursor)
    get_page = AsyncMock(return_value=page)
    monkeypatch.setattr(PaidCampaignService, "get_candidate_page", get_page)
    monkeypatch.setattr(graphql, "PAID_PUBLIC_DELIVERY_ENABLED", True)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        AsyncMock(return_value=[]),
    )
    selection_ids = [uuid.uuid4(), uuid.uuid4()]
    select_candidate = AsyncMock(side_effect=selection_ids)
    monkeypatch.setattr(PaidDeliveryService, "select_candidate", select_candidate)
    interaction_contexts = ["paid-context-1", "paid-context-2"]
    issue_context = AsyncMock(side_effect=interaction_contexts)
    monkeypatch.setattr(
        "services.paid_interaction_context_service.PaidInteractionContextService.issue",
        issue_context,
    )
    record_impression = AsyncMock(
        side_effect=AssertionError("Fetching the Paid feed must not record impressions")
    )
    monkeypatch.setattr(PaidDeliveryService, "record_impression", record_impression)
    forbidden = AsyncMock(side_effect=AssertionError("Paid delivery used a normal feed"))
    for handler in ("_organic_feed", "_viral_feed", "_community_feed", "_for_you_feed"):
        monkeypatch.setattr(graphql, handler, forbidden)

    result = await _feed(AppContext(db, viewer), cursor, 2, False, FeedAlgorithm.PAID)

    assert isinstance(result, FeedPageType)
    assert result.next_cursor == cursor
    assert [item.id for item in result.items] == [first_post.id, second_post.id]
    assert [item.caption for item in result.items] == ["", ""]
    assert [item.creator.username for item in result.items] == [
        first_creator.username,
        second_creator.username,
    ]
    assert [item.creator.display_name for item in result.items] == [
        "First Creator",
        "Second Creator",
    ]
    assert all(item.is_sponsored for item in result.items)
    assert [item.sponsored_label for item in result.items] == ["Sponsored", "Sponsored"]
    assert [item.paid_campaign_id for item in result.items] == [
        candidate.paid_campaign_id for candidate in candidates
    ]
    assert [item.paid_delivery_id for item in result.items] == selection_ids
    assert [item.paid_interaction_context for item in result.items] == interaction_contexts
    get_page.assert_awaited_once_with(viewer, cursor=cursor, limit=2)
    assert [call.args for call in select_candidate.await_args_list] == [
        (viewer, candidate) for candidate in candidates
    ]
    assert [call.args for call in issue_context.await_args_list] == [
        (viewer.id, delivery_id) for delivery_id in selection_ids
    ]
    record_impression.assert_not_awaited()
    forbidden.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_selection_failure_rolls_back_and_returns_no_feed_page(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    creator, post = await user_and_post(db)
    await db.commit()
    candidate = PaidCandidate(post_id=post.id, paid_campaign_id=uuid.uuid4())
    monkeypatch.setattr(
        PaidCampaignService,
        "get_candidate_page",
        AsyncMock(return_value=PaidCandidatePage(items=[candidate])),
    )
    monkeypatch.setattr(graphql, "PAID_PUBLIC_DELIVERY_ENABLED", True)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        AsyncMock(return_value=[]),
    )
    selection_id = uuid.uuid4()
    select_candidate = AsyncMock(side_effect=ValueError("delivery eligibility changed"))
    monkeypatch.setattr(PaidDeliveryService, "select_candidate", select_candidate)
    rollback = AsyncMock(wraps=db.rollback)
    monkeypatch.setattr(db, "rollback", rollback)

    with pytest.raises(ValueError, match="delivery eligibility changed"):
        await _feed(AppContext(db, viewer), None, 10, False, FeedAlgorithm.PAID)

    select_candidate.assert_awaited_once_with(viewer, candidate)
    rollback.assert_awaited_once()
    assert await db.get(type(post), post.id) == post
    assert await db.get(type(creator), creator.id) == creator


@pytest.mark.asyncio
async def test_recording_revalidates_eligibility_before_accounting(db, monkeypatch):
    viewer, post = await user_and_post(db)
    await db.commit()
    subject = campaign(owner_id=uuid.uuid4(), post_id=post.id)
    selection = PaidDelivery(
        id=uuid.uuid4(),
        campaign_id=subject.id,
        viewer_id=viewer.id,
        post_id=post.id,
        selected_at=NOW,
    )
    service = PaidDeliveryService(db)
    repository = service.repository
    monkeypatch.setattr(repository, "require_postgresql", lambda: None)
    monkeypatch.setattr(repository, "get_selection", AsyncMock(return_value=selection))
    monkeypatch.setattr(repository, "lock_campaign", AsyncMock(return_value=subject))
    monkeypatch.setattr(repository, "server_time", AsyncMock(return_value=NOW))
    record_locked = AsyncMock()
    monkeypatch.setattr(repository, "record_locked", record_locked)
    monkeypatch.setattr(service, "_viewer", AsyncMock(return_value=viewer))
    validate = AsyncMock()
    monkeypatch.setattr(service, "_validate", validate)

    receipt = await service.record_impression(viewer, selection.id)

    assert receipt.delivery_id == selection.id
    validate.assert_awaited_once_with(subject, viewer, NOW)
    record_locked.assert_awaited_once_with(subject, selection, NOW)


@pytest.mark.asyncio
async def test_failed_eligibility_revalidation_prevents_accounting(db, monkeypatch):
    viewer, post = await user_and_post(db)
    await db.commit()
    subject = campaign(owner_id=uuid.uuid4(), post_id=post.id)
    selection = PaidDelivery(
        id=uuid.uuid4(),
        campaign_id=subject.id,
        viewer_id=viewer.id,
        post_id=post.id,
        selected_at=NOW,
    )
    service = PaidDeliveryService(db)
    repository = service.repository
    monkeypatch.setattr(repository, "require_postgresql", lambda: None)
    monkeypatch.setattr(repository, "get_selection", AsyncMock(return_value=selection))
    monkeypatch.setattr(repository, "lock_campaign", AsyncMock(return_value=subject))
    monkeypatch.setattr(repository, "server_time", AsyncMock(return_value=NOW))
    record_locked = AsyncMock()
    monkeypatch.setattr(repository, "record_locked", record_locked)
    monkeypatch.setattr(service, "_viewer", AsyncMock(return_value=viewer))
    monkeypatch.setattr(
        service, "_validate", AsyncMock(side_effect=ValueError("campaign is no longer eligible"))
    )

    with pytest.raises(ValueError, match="no longer eligible"):
        await service.record_impression(viewer, selection.id)

    record_locked.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_paid_gate_still_returns_not_implemented(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    get_page = AsyncMock(return_value=PaidCandidatePage(items=[]))
    monkeypatch.setattr(PaidCampaignService, "get_candidate_page", get_page)
    select_candidate = AsyncMock()
    record_impression = AsyncMock()
    monkeypatch.setattr(PaidDeliveryService, "select_candidate", select_candidate)
    monkeypatch.setattr(PaidDeliveryService, "record_impression", record_impression)
    monkeypatch.setattr(
        "repositories.social_repository.FollowRepository.get_following_ids",
        AsyncMock(return_value=[]),
    )

    assert graphql.PAID_PUBLIC_DELIVERY_ENABLED is False
    result = await schema.execute(
        "{ feed(filter: {algorithm: PAID}) { items { id isSponsored } } }",
        context_value=AppContext(db, viewer),
    )

    assert result.data is None
    assert result.errors and len(result.errors) == 1
    assert result.errors[0].message == "Paid feed delivery is disabled"
    assert result.errors[0].extensions is not None
    assert result.errors[0].extensions["code"] == "NOT_IMPLEMENTED"
    assert result.errors[0].extensions["statusCode"] == 501
    get_page.assert_awaited_once_with(viewer, cursor=None, limit=10)
    select_candidate.assert_not_awaited()
    record_impression.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_interaction_route_preserves_server_validated_delivery_context(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    paid_delivery_id = uuid.uuid4()
    like_result = graphql.LikeResultType(liked=True, likes=1)
    like = AsyncMock(return_value=like_result)
    monkeypatch.setattr(graphql, "_like_post_legacy", like)
    context_token = "server-issued-context"
    consume_context = AsyncMock()
    monkeypatch.setattr(
        "services.paid_interaction_context_service.PaidInteractionContextService.consume",
        consume_context,
    )
    post_id = uuid.uuid4()

    result = await schema.execute(
        """
        mutation PaidLike($id: UUID!, $delivery: UUID!, $context: String!) {
          paidLikePost(id: $id, paidDeliveryId: $delivery, paidInteractionContext: $context) {
            liked likes
          }
        }
        """,
        variable_values={
            "id": str(post_id),
            "delivery": str(paid_delivery_id),
            "context": context_token,
        },
        context_value=AppContext(db, viewer),
    )

    assert result.errors is None
    assert result.data == {"paidLikePost": {"liked": True, "likes": 1}}
    like.assert_awaited_once()
    assert like.await_args is not None
    assert like.await_args.kwargs == {
        "like": True,
        "paid_delivery_id": paid_delivery_id,
    }
    consume_context.assert_awaited_once_with(
        context_token,
        viewer_id=viewer.id,
        post_id=post_id,
        delivery_id=paid_delivery_id,
    )


@pytest.mark.asyncio
async def test_paid_interaction_requires_server_issued_context_argument(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    like = AsyncMock(return_value=graphql.LikeResultType(liked=True, likes=1))
    consume_context = AsyncMock()
    monkeypatch.setattr(graphql, "_like_post_legacy", like)
    monkeypatch.setattr(
        "services.paid_interaction_context_service.PaidInteractionContextService.consume",
        consume_context,
    )

    result = await schema.execute(
        """
        mutation PaidLike($id: UUID!, $delivery: UUID!) {
          paidLikePost(id: $id, paidDeliveryId: $delivery) { liked likes }
        }
        """,
        variable_values={"id": str(uuid.uuid4()), "delivery": str(uuid.uuid4())},
        context_value=AppContext(db, viewer),
    )

    assert result.data is None
    assert result.errors
    assert "paidInteractionContext" in result.errors[0].message
    like.assert_not_awaited()
    consume_context.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_paid_interaction_cannot_replay_paid_delivery_id(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    like = AsyncMock(return_value=graphql.LikeResultType(liked=True, likes=1))
    monkeypatch.setattr(graphql, "_like_post_legacy", like)
    paid_delivery_id = uuid.uuid4()

    result = await schema.execute(
        """
        mutation Replay($id: UUID!, $delivery: UUID!) {
          likePost(id: $id, paidDeliveryId: $delivery) { liked likes }
        }
        """,
        variable_values={"id": str(uuid.uuid4()), "delivery": str(paid_delivery_id)},
        context_value=AppContext(db, viewer),
    )

    assert result.data is None
    assert result.errors
    assert "Unknown argument 'paidDeliveryId'" in result.errors[0].message
    like.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_interaction_without_paid_delivery_remains_non_paid(db, monkeypatch):
    viewer, _ = await user_and_post(db)
    like = AsyncMock(return_value=graphql.LikeResultType(liked=True, likes=1))
    monkeypatch.setattr(graphql, "_like_post_legacy", like)

    result = await schema.execute(
        """
        mutation Normal($id: UUID!) {
          likePost(id: $id) { liked likes }
        }
        """,
        variable_values={"id": str(uuid.uuid4())},
        context_value=AppContext(db, viewer),
    )

    assert result.errors is None
    like.assert_awaited_once()
    assert like.await_args is not None
    assert like.await_args.kwargs == {"like": True}
