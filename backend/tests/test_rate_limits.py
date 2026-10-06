"""Focused action-limit and active HTTP/GraphQL integration checks."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from strawberry.http import GraphQLRequestData

import api.graphql as graphql
import api.graphql_rate_limits as graphql_rate_limits
from api.graphql_rate_limits import MUTATION_ACTIONS, MUTATION_LIMITS, mutation_costs
from app.errors import register_exception_handlers
from app.main import RateLimitMiddleware, create_app
from app.rate_limits import (
    RATE_LIMIT_MESSAGE,
    ActionLimit,
    ActionRateLimiter,
    ActionRateLimitExceeded,
    client_identity,
)

ID = "00000000-0000-0000-0000-000000000001"
FOLLOW = 'follow(username: "creator") { __typename }'
SHARE = f'sharePost(id: "{ID}") {{ __typename }}'
LOGIN = 'login(input: {email: "test@example.com", password: "test"}) { __typename }'
REGISTER = (
    'register(input: {email: "test@example.com", username: "test", password: "test"}) '
    "{ __typename }"
)
COMMENT = f'createComment(input: {{postId: "{ID}", body: "hi"}}) {{ __typename }}'
ADD_COMMENT = f'addComment(postId: "{ID}", text: "hi") {{ __typename }}'

PROTECTED_FIELDS = [
    ("register", REGISTER, "_register"),
    ("login", LOGIN, "_login"),
    (
        "createPost",
        f'createPost(input: {{mediaId: "{ID}", thumbnailMediaId: "{ID}", audio: "", '
        'visibility: "public", allowComments: true, allowCollabs: true, durationSec: 1, '
        'status: "published"}) { __typename }',
        "_create_post_legacy",
    ),
    ("createComment", COMMENT, "_create_comment"),
    ("addComment", ADD_COMMENT, "_add_comment"),
    ("sharePost", SHARE, "_share_post_legacy"),
    ("follow", FOLLOW, "_follow"),
    (
        "createCollaboration",
        'createCollaboration(input: {title: "proposal", participantIds: []}) { __typename }',
        "_create_collaboration",
    ),
    (
        "sendMessage",
        f'sendMessage(input: {{conversationId: "{ID}", body: "hi"}}) {{ __typename }}',
        "_send_message",
    ),
    ("startLiveStream", 'startLiveStream(title: "live") { __typename }', "_start_live_stream"),
]


def query_data(query, variables=None, operation_name=None):
    return GraphQLRequestData(
        query=query, variables=variables, operation_name=operation_name, extensions=None
    )


def assert_limited(response, retry_after=60):
    assert response.status_code == 429
    assert response.json() == {
        "error": {"code": "TOO_MANY_REQUESTS", "message": RATE_LIMIT_MESSAGE}
    }
    assert int(response.headers["Retry-After"]) == retry_after


@pytest.fixture
def harness(monkeypatch):
    now = [100.0]
    router = graphql.create_graphql_router(Mock(return_value=AsyncMock()))
    router.action_limiter = ActionRateLimiter(MUTATION_LIMITS, clock=lambda: now[0])
    calls = {}
    for _, _, resolver in PROTECTED_FIELDS:
        calls[resolver] = AsyncMock(return_value=SimpleNamespace())
        monkeypatch.setattr(graphql, resolver, calls[resolver])

    user = graphql.User(id=UUID(ID))
    second_user = graphql.User(id=UUID(int=2))

    async def authenticate(_db, token):
        if token in {"user-a", "user-a-new-token"}:
            return user, token
        if token == "user-b":
            return second_user, token
        return None, None

    monkeypatch.setattr(graphql, "_graphql_user_from_token", authenticate)
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RateLimitMiddleware)  # The active middleware's default is 60/IP/min.
    app.include_router(router, prefix="/graphql")

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return SimpleNamespace(app=app, router=router, calls=calls, now=now)


@pytest_asyncio.fixture
async def client(harness):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.1", 1234)),
        base_url="http://test",
    ) as client:
        yield client


async def mutate(client, field=FOLLOW, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return await client.post("/graphql", json={"query": f"mutation {{ {field} }}"}, headers=headers)


@pytest.mark.asyncio
async def test_authenticated_limits_follow_user_across_tokens_and_ips(client, harness):
    for _ in range(20):
        assert (await mutate(client, token="user-a")).status_code == 200
    assert_limited(await mutate(client, token="user-a-new-token"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.2", 1234)),
        base_url="http://test",
    ) as other_ip:
        assert_limited(await mutate(other_ip, token="user-a"))
    assert (await mutate(client, token="user-b")).status_code == 200
    assert harness.calls["_follow"].await_count == 21


@pytest.mark.asyncio
async def test_anonymous_and_invalid_tokens_share_direct_ip_limit(client, harness):
    for _ in range(5):
        assert (await mutate(client, LOGIN)).status_code == 200
    assert_limited(await mutate(client, LOGIN, token="invalid"))
    response = await client.post(
        "/graphql",
        json={"query": f"mutation {{ {LOGIN} }}"},
        headers={"X-Forwarded-For": "192.0.2.99"},
    )
    assert_limited(response)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.2", 1234)),
        base_url="http://test",
    ) as other_ip:
        assert (await mutate(other_ip, LOGIN)).status_code == 200
    assert harness.calls["_login"].await_count == 6


@pytest.mark.asyncio
async def test_authenticated_and_anonymous_buckets_do_not_collide(client):
    for _ in range(5):
        assert (await mutate(client, LOGIN)).status_code == 200
    assert_limited(await mutate(client, LOGIN))
    assert (await mutate(client, LOGIN, token="user-a")).status_code == 200


@pytest.mark.asyncio
async def test_separate_actions_have_separate_buckets(client, harness):
    for _ in range(20):
        assert (await mutate(client, token="user-a")).status_code == 200
    assert_limited(await mutate(client, token="user-a"))
    assert (await mutate(client, SHARE, token="user-a")).status_code == 200
    assert harness.calls["_share_post_legacy"].await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("name,field,resolver", PROTECTED_FIELDS)
async def test_every_protected_field_enforces_exact_default_limit(
    client, harness, name, field, resolver
):
    limit = MUTATION_LIMITS[MUTATION_ACTIONS[name]].requests
    for _ in range(limit):
        response = await mutate(client, field, token="user-a")
        assert response.status_code == 200
        assert "errors" not in response.json(), response.json()
    assert_limited(await mutate(client, field, token="user-a"))
    assert harness.calls[resolver].await_count == limit


@pytest.mark.asyncio
async def test_auth_mutations_share_one_action_bucket(client, harness):
    for _ in range(5):
        assert (await mutate(client, LOGIN)).status_code == 200
    assert_limited(await mutate(client, REGISTER))
    harness.calls["_register"].assert_not_awaited()


@pytest.mark.asyncio
async def test_comment_creation_entry_points_share_bucket(client, harness):
    fields = " ".join(f"c{i}: {COMMENT}" for i in range(20))
    response = await mutate(client, fields, token="user-a")
    assert response.status_code == 200
    assert "errors" not in response.json(), response.json()
    assert_limited(await mutate(client, ADD_COMMENT, token="user-a"))
    harness.calls["_add_comment"].assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_aliases_charge_each_execution_and_reject_atomically(client, harness):
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(19))
    assert (await mutate(client, fields, token="user-a")).status_code == 200
    response = await mutate(client, f"a: {FOLLOW} b: {FOLLOW} other: {SHARE}", token="user-a")
    assert_limited(response)
    assert harness.calls["_follow"].await_count == 19
    harness.calls["_share_post_legacy"].assert_not_awaited()
    assert (await mutate(client, token="user-a")).status_code == 200
    assert (await mutate(client, SHARE, token="user-a")).status_code == 200
    assert_limited(await mutate(client, token="user-a"))


@pytest.mark.asyncio
async def test_single_request_larger_than_limit_runs_no_resolvers(client, harness):
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(21))
    assert_limited(await mutate(client, fields, token="user-a"))
    harness.calls["_follow"].assert_not_awaited()
    assert (await mutate(client, token="user-a")).status_code == 200


@pytest.mark.asyncio
async def test_fragments_directives_and_variable_defaults_match_execution(client, harness):
    document = f"""
        mutation Selected($skip: Boolean! = true, $include: Boolean! = true) {{
            ...Root
            ... on Mutation {{
                b: follow(username: "creator") @include(if: $include) {{ __typename }}
            }}
            c: follow(username: "creator") @skip(if: $skip) {{ __typename }}
        }}
        fragment Root on Mutation {{ a: {FOLLOW} }}
    """
    response = await client.post(
        "/graphql", json={"query": document}, headers={"Authorization": "Bearer user-a"}
    )
    assert response.status_code == 200
    assert "errors" not in response.json(), response.json()
    assert harness.calls["_follow"].await_count == 2
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(18))
    assert (await mutate(client, fields, token="user-a")).status_code == 200
    assert_limited(await mutate(client, token="user-a"))


@pytest.mark.asyncio
async def test_selected_operation_and_merged_fields_count_only_executions(client, harness):
    document = f"""
        mutation Selected {{ same: {FOLLOW} ...Root }}
        mutation Unused {{ a: {FOLLOW} b: {FOLLOW} }}
        fragment Root on Mutation {{ same: {FOLLOW} }}
    """
    response = await client.post(
        "/graphql",
        json={"query": document, "operationName": "Selected"},
        headers={"Authorization": "Bearer user-a"},
    )
    assert response.status_code == 200
    assert "errors" not in response.json(), response.json()
    assert harness.calls["_follow"].await_count == 1
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(19))
    assert (await mutate(client, fields, token="user-a")).status_code == 200
    assert_limited(await mutate(client, token="user-a"))


@pytest.mark.asyncio
async def test_query_does_not_consume_action_quota(client):
    response = await client.post("/graphql", json={"query": "{ __typename }"})
    assert response.status_code == 200
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(20))
    response = await mutate(client, fields, token="user-a")
    assert response.status_code == 200
    assert "errors" not in response.json(), response.json()


@pytest.mark.asyncio
async def test_query_at_depth_limit_is_allowed(client, monkeypatch):
    monkeypatch.setattr(graphql_rate_limits, "MAX_QUERY_DEPTH", 1)
    response = await client.post("/graphql", json={"query": "{ __typename }"})
    assert response.status_code == 200
    assert response.json() == {"data": {"__typename": "Query"}}


@pytest.mark.asyncio
async def test_query_over_depth_limit_is_rejected_before_resolver(client, harness, monkeypatch):
    monkeypatch.setattr(graphql_rate_limits, "MAX_QUERY_DEPTH", 4)
    discover_creators = AsyncMock()
    monkeypatch.setattr(graphql, "_discover_creators", discover_creators)
    response = await client.post(
        "/graphql",
        json={
            "query": """
                {
                    discoverCreators {
                        edges {
                            node {
                                user { id }
                            }
                        }
                    }
                }
            """
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] is None
    assert body["errors"][0]["extensions"]["code"] == "QUERY_DEPTH_LIMIT_EXCEEDED"
    assert body["errors"][0]["extensions"]["statusCode"] == 400
    discover_creators.assert_not_awaited()


@pytest.mark.asyncio
async def test_excessive_selected_fields_are_rejected_before_mutation(client, harness, monkeypatch):
    monkeypatch.setattr(graphql_rate_limits, "MAX_SELECTED_FIELDS", 3)
    response = await mutate(
        client,
        f"a: {FOLLOW} b: {FOLLOW}",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"] is None
    assert body["errors"][0]["extensions"]["code"] == "QUERY_FIELD_LIMIT_EXCEEDED"
    harness.calls["_follow"].assert_not_awaited()


@pytest.mark.asyncio
async def test_fragments_within_field_limit_are_allowed(client, monkeypatch):
    monkeypatch.setattr(graphql_rate_limits, "MAX_SELECTED_FIELDS", 1)
    response = await client.post(
        "/graphql",
        json={"query": "query { ...TypeName } fragment TypeName on Query { __typename }"},
    )
    assert response.status_code == 200
    assert response.json() == {"data": {"__typename": "Query"}}


@pytest.mark.asyncio
async def test_aliases_within_field_limit_are_allowed(client, monkeypatch):
    monkeypatch.setattr(graphql_rate_limits, "MAX_SELECTED_FIELDS", 2)
    response = await client.post(
        "/graphql",
        json={"query": "query { first: __typename second: __typename }"},
    )
    assert response.status_code == 200
    assert response.json() == {"data": {"first": "Query", "second": "Query"}}


@pytest.mark.asyncio
async def test_normal_mutation_still_executes_under_complexity_limits(client, harness):
    response = await mutate(client)
    assert response.status_code == 200
    assert "errors" not in response.json(), response.json()
    assert harness.calls["_follow"].await_count == 1


@pytest.mark.asyncio
async def test_invalid_documents_and_variables_preserve_quota(client, harness):
    for document, variables in [
        ("mutation {", None),
        ("mutation { unknownMutation }", None),
        ("mutation($name: String!) { follow(username: $name) { __typename } }", {}),
    ]:
        response = await client.post("/graphql", json={"query": document, "variables": variables})
        assert response.status_code == 200
        assert "errors" in response.json()
    harness.calls["_follow"].assert_not_awaited()
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(20))
    response = await mutate(client, fields)
    assert response.status_code == 200
    assert "errors" not in response.json(), response.json()


@pytest.mark.asyncio
async def test_get_mutation_is_rejected_without_consuming_action_quota(client, harness):
    response = await client.get("/graphql", params={"query": f"mutation {{ {FOLLOW} }}"})
    assert response.status_code == 400
    harness.calls["_follow"].assert_not_awaited()
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(20))
    assert (await mutate(client, fields)).status_code == 200


@pytest.mark.asyncio
async def test_failed_resolver_attempts_still_consume_quota(client, harness):
    harness.calls["_login"].side_effect = ValueError("Invalid credentials")
    for _ in range(5):
        response = await mutate(client, LOGIN)
        assert response.status_code == 200
        assert "errors" in response.json()
    assert_limited(await mutate(client, LOGIN))
    assert harness.calls["_login"].await_count == 5


@pytest.mark.asyncio
async def test_retry_after_and_action_window_recovery(client, harness):
    for _ in range(5):
        assert (await mutate(client, LOGIN)).status_code == 200
    harness.now[0] += 59.1
    assert_limited(await mutate(client, LOGIN), retry_after=1)
    harness.now[0] = 160.0
    assert (await mutate(client, LOGIN)).status_code == 200


@pytest.mark.asyncio
async def test_json_batch_cannot_bypass_limits(client, harness):
    response = await client.post("/graphql", json=[{"query": f"mutation {{ {FOLLOW} }}"}] * 21)
    assert response.status_code == 400
    harness.calls["_follow"].assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_json_batch_aggregates_all_operations(client, harness, monkeypatch):
    monkeypatch.setattr(graphql.schema.config, "batching_config", {"max_operations": 30})
    response = await client.post(
        "/graphql",
        json=[{"query": f"mutation {{ {FOLLOW} }}"}] * 21,
        headers={"Authorization": "Bearer user-a"},
    )
    assert_limited(response)
    harness.calls["_follow"].assert_not_awaited()
    response = await client.post(
        "/graphql",
        json=[{"query": f"mutation {{ {FOLLOW} }}"}] * 20,
        headers={"Authorization": "Bearer user-a"},
    )
    assert response.status_code == 200
    assert all("errors" not in result for result in response.json()), response.json()
    assert harness.calls["_follow"].await_count == 20
    assert_limited(await mutate(client, token="user-a"))


@pytest.mark.asyncio
async def test_normal_mutation_data_and_unprotected_actions_are_preserved(
    client, harness, monkeypatch
):
    result = graphql.FollowResultType(following=True, followers=2, following_count=1)
    harness.calls["_follow"].return_value = result
    unfollow = AsyncMock(return_value=result)
    monkeypatch.setattr(graphql, "_unfollow", unfollow)
    response = await mutate(
        client, 'follow(username: "creator") { following followers followingCount }'
    )
    assert response.json() == {
        "data": {"follow": {"following": True, "followers": 2, "followingCount": 1}}
    }
    fields = " ".join(f"f{i}: {FOLLOW}" for i in range(19))
    assert (await mutate(client, fields)).status_code == 200
    assert_limited(await mutate(client))
    fields = " ".join(f'f{i}: unfollow(username: "creator") {{ following }}' for i in range(25))
    response = await mutate(client, fields)
    assert response.status_code == 200
    assert "errors" not in response.json(), response.json()
    assert unfollow.await_count == 25


@pytest.mark.asyncio
async def test_global_window_recovers_and_retry_after_rounds_up(client, monkeypatch):
    now = [100.0]
    monkeypatch.setattr("app.main.time", SimpleNamespace(monotonic=lambda: now[0]))
    for _ in range(60):
        assert (await client.post("/graphql", json={"query": "{ __typename }"})).status_code == 200
    now[0] = 159.1
    response = await client.post("/graphql", json={"query": "{ __typename }"})
    assert_limited(response, retry_after=1)
    now[0] = 160.0
    response = await client.post("/graphql", json={"query": "{ __typename }"})
    assert response.status_code == 200
    assert response.headers["X-RateLimit-Remaining"] == "59"


@pytest.mark.asyncio
async def test_global_default_still_limits_60_requests_per_ip(client, harness):
    for remaining in range(59, -1, -1):
        response = await client.post("/graphql", json={"query": "{ __typename }"})
        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "60"
        assert int(response.headers["X-RateLimit-Remaining"]) == remaining
    response = await client.post(
        "/graphql", json={"query": "{ __typename }"}, headers={"Authorization": "Bearer user-b"}
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    assert (await client.get("/health")).status_code == 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app, client=("192.0.2.2", 1234)),
        base_url="http://test",
    ) as other_ip:
        assert (
            await other_ip.post("/graphql", json={"query": "{ __typename }"})
        ).status_code == 200


@pytest.mark.asyncio
async def test_action_rejections_still_count_toward_global_limit(client):
    for _ in range(5):
        assert (await mutate(client, LOGIN)).status_code == 200
    for _ in range(55):
        response = await mutate(client, LOGIN)
        assert_limited(response)
        assert "X-RateLimit-Remaining" in response.headers
    response = await client.post("/graphql", json={"query": "{ __typename }"})
    assert response.status_code == 429
    assert "X-RateLimit-Remaining" not in response.headers


@pytest.mark.asyncio
async def test_app_factory_wires_action_and_global_limits(monkeypatch):
    monkeypatch.setattr("app.main.settings.rate_limit_per_minute", 60)
    monkeypatch.setattr(graphql, "_follow", AsyncMock(return_value=SimpleNamespace()))
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        fields = " ".join(f"f{i}: {FOLLOW}" for i in range(21))
        response = await mutate(client, fields)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "TOO_MANY_REQUESTS"
        assert response.headers["X-RateLimit-Limit"] == "60"
        assert int(response.headers["Retry-After"]) == 60


def test_identity_namespaces_prevent_user_ip_collisions():
    assert client_identity("192.0.2.1", "unused") != client_identity(None, "192.0.2.1")


def test_retry_after_waits_for_enough_quota_and_expired_buckets_are_cleaned():
    now = [0.0]
    limiter = ActionRateLimiter({"action": ActionLimit(3)}, clock=lambda: now[0])
    identity = client_identity(None, "192.0.2.1")
    limiter.consume(identity, {"action": 1})
    now[0] = 10.0
    limiter.consume(identity, {"action": 2})
    now[0] = 59.0
    with pytest.raises(ActionRateLimitExceeded) as error:
        limiter.consume(identity, {"action": 2})
    assert error.value.retry_after == 11
    now[0] = 70.0
    limiter.consume(client_identity(None, "192.0.2.2"), {"action": 1})
    assert (identity, "action") not in limiter._history


def test_mixed_action_reservations_are_atomic():
    limiter = ActionRateLimiter({"a": ActionLimit(1), "b": ActionLimit(1)})
    identity = client_identity(None, "192.0.2.1")
    limiter.consume(identity, {"a": 1})
    with pytest.raises(ActionRateLimitExceeded):
        limiter.consume(identity, {"a": 1, "b": 1})
    limiter.consume(identity, {"b": 1})


def test_mutation_costs_use_validated_schema_and_underlying_field_names():
    costs = mutation_costs(
        graphql.schema._schema,
        query_data(f"mutation {{ x: {LOGIN} y: {REGISTER} }}"),
    )
    assert costs == {"auth": 2}
    assert mutation_costs(graphql.schema._schema, query_data("{ __typename }")) == {}
