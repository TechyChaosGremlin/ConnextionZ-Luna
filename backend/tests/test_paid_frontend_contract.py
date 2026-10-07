"""Validate the shipped frontend query against the real, still-disabled Paid path."""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from graphql import GraphQLObjectType, get_named_type, parse, validate
from sqlalchemy import MetaData, Table, Uuid, select
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from api.graphql import AppContext, schema
from app.models.analytics import AnalyticsEvent
from app.models.paid_delivery import PaidDelivery
from app.models.social import Follow
from app.models.user import User
from services.paid_campaign_service import PaidCampaignService
from tests.test_paid_campaigns import campaign
from tests.test_paid_campaigns import db as db
from tests.test_paid_campaigns import user_and_post

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_shipped_paid_query_schema_cursor_and_disabled_response_match_frontend(
    db, monkeypatch
):
    source = (ROOT / "src" / "app" / "paid-feed-graphql.ts").read_text(encoding="utf-8")
    match = re.search(r"export const PAID_FEED_QUERY = `([^`]+)`;", source)
    assert match is not None
    query = match.group(1)
    graphql_schema = schema._schema
    assert validate(graphql_schema, parse(query)) == []
    assert graphql_schema.query_type is not None
    feed = graphql_schema.query_type.fields["feed"]
    assert str(feed.args["cursor"].type) == "String"
    assert str(feed.args["limit"].type) == "Int!"
    page_type = get_named_type(feed.type)
    assert isinstance(page_type, GraphQLObjectType)
    assert str(page_type.fields["nextCursor"].type) == "String"
    assert str(page_type.fields["items"].type) == "[FeedItemType!]!"
    item_type = get_named_type(page_type.fields["items"].type)
    assert isinstance(item_type, GraphQLObjectType)
    assert str(item_type.fields["isSponsored"].type) == "Boolean!"
    assert str(item_type.fields["sponsoredLabel"].type) == "String"
    assert str(item_type.fields["paidCampaignId"].type) == "UUID"
    assert str(item_type.fields["paidDeliveryId"].type) == "UUID"
    assert str(item_type.fields["paidInteractionContext"].type) == "String"
    assert str(item_type.fields["creator"].type) == "ProfileSummaryType!"

    metadata = MetaData()
    assert isinstance(User.__table__, Table)
    assert isinstance(Follow.__table__, Table)
    User.__table__.to_metadata(metadata)
    follows = Follow.__table__.to_metadata(metadata)
    for column in follows.columns:
        if isinstance(column.type, PG_UUID):
            column.type = Uuid(native_uuid=False)
    connection = await db.connection()
    await connection.run_sync(follows.create)

    viewer, _ = await user_and_post(db)
    now = datetime.now(timezone.utc)
    subjects = []
    for identity in (2, 1):
        owner, post = await user_and_post(db)
        subject = campaign(
            id=uuid.UUID(int=identity),
            owner_id=owner.id,
            post_id=post.id,
            start_at=now - timedelta(hours=1),
            end_at=now + timedelta(days=1),
        )
        db.add(subject)
        subjects.append(subject)
    await db.commit()
    original_page = PaidCampaignService.get_candidate_page
    calls = []

    async def observe_page(self, user, **kwargs):
        page = await original_page(self, user, **kwargs)
        calls.append((kwargs, page))
        return page

    monkeypatch.setattr(PaidCampaignService, "get_candidate_page", observe_page)
    forbidden = AsyncMock(side_effect=AssertionError("Paid must not fall back to normal feeds"))
    for handler in ("_organic_feed", "_viral_feed", "_community_feed", "_for_you_feed"):
        monkeypatch.setattr(f"api.graphql.{handler}", forbidden)

    first = await schema.execute(
        query, variable_values={"cursor": None, "limit": 1}, context_value=AppContext(db, viewer)
    )
    assert len(calls) == 1
    assert calls[0][0] == {"cursor": None, "limit": 1}
    candidate_page = calls[0][1]
    assert [item.paid_campaign_id.int for item in candidate_page.items] == [1]
    assert candidate_page.items[0].is_sponsored is True
    assert candidate_page.items[0].sponsored_label == "Sponsored"
    cursor = candidate_page.next_cursor
    assert isinstance(cursor, str) and cursor.startswith("paid1.")
    continuation = await schema.execute(
        query, variable_values={"cursor": cursor, "limit": 1}, context_value=AppContext(db, viewer)
    )
    assert len(calls) == 2
    assert calls[1][0] == {"cursor": cursor, "limit": 1}
    assert [item.paid_campaign_id.int for item in calls[1][1].items] == [2]
    assert calls[1][1].next_cursor is None
    for result in (first, continuation):
        assert result.data is None
        assert result.errors is not None and len(result.errors) == 1
        error = result.errors[0]
        assert error.message == "Paid feed delivery is disabled"
        assert error.path == ["feed"]
        assert error.extensions is not None
        assert error.extensions["code"] == "NOT_IMPLEMENTED"
        assert error.extensions["statusCode"] == 501
    forbidden.assert_not_awaited()
    assert not db.new and not db.dirty and not db.deleted
    assert all(
        subject.impressions_delivered == subject.reach_delivered == 0 for subject in subjects
    )
    assert (await db.scalars(select(PaidDelivery))).all() == []
    assert (await db.scalars(select(AnalyticsEvent))).all() == []

    assert first.errors is not None
    frontend = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { PAID_FEED_QUERY, fetchPaidFeedPage } from './src/app/paid-feed-graphql.ts';
import { createPaidFeedStore } from './src/app/paid-feed-store.ts';
const contract = JSON.parse(readFileSync(0, 'utf8'));
assert.equal(PAID_FEED_QUERY, contract.query);
const variables = [];
globalThis.fetch = async (_input, init) => {
  const body = JSON.parse(init.body);
  assert.equal(body.query, PAID_FEED_QUERY);
  variables.push(body.variables);
  return Response.json(contract.response);
};
const result = await fetchPaidFeedPage(contract.cursor, 1);
assert.deepEqual(result, { ok: false, error: 'Paid feed delivery is disabled' });
assert.deepEqual(variables[0], { cursor: contract.cursor, limit: 1 });
const store = createPaidFeedStore(1);
await store.reload();
assert.equal(store.getSnapshot().status, 'error');
assert.equal(store.getSnapshot().error, 'Paid feed delivery is disabled');
assert.deepEqual(store.getSnapshot().items, []);
await store.loadMore();
assert.equal(variables.length, 2);
assert.deepEqual(variables[1], { cursor: null, limit: 1 });
""",
        ],
        input=json.dumps(
            {
                "query": query,
                "cursor": cursor,
                "response": {
                    "data": first.data,
                    "errors": [error.formatted for error in first.errors],
                },
            }
        ),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert frontend.returncode == 0, frontend.stdout + frontend.stderr
