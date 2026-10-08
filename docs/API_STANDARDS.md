# ============================================================================
# ConnextionZ Platform — API Standards
# ============================================================================
#
# This document defines the API standards and conventions for the ConnextionZ
# platform. All developers (frontend and backend) must adhere to these standards
# when building or consuming APIs.
#
# ============================================================================

## 1. API Protocols

| Protocol | Use Case | Notes |
|----------|----------|-------|
| **GraphQL** | Primary data-fetching protocol for web and mobile clients | Served at `POST /api/graphql`; GraphiQL at `GET /api/graphql` |
| **REST** | Auth endpoints, webhooks, health checks, OAuth callbacks | Served at well-known paths (`/auth/*`, `/health`, etc.) |
| **WebSocket** | Real-time features: messaging, notifications, live streaming | Socket.IO for web; native WebSocket for mobile |
| **GraphQL Subscriptions** | Real-time feed updates, collaboration status changes | GraphQL-WS protocol via Strawberry |

## 2. GraphQL Conventions

### 2.1 Request Format

All GraphQL requests use the standard POST method with `application/json` content type:

```json
{
  "query": "query GetFeed($first: Int!, $after: String) { feed(first: $first, after: $after) { edges { node { id title } } pageInfo { hasNextPage } } }",
  "variables": { "first": 20, "after": null },
  "operationName": "GetFeed"
}
```

### 2.2 Error Format

All GraphQL errors follow the GraphQL spec with a consistent `extensions` shape:

```json
{
  "errors": [
    {
      "message": "User not found",
      "locations": [{ "line": 2, "column": 3 }],
      "path": ["profile"],
      "extensions": {
        "code": "NOT_FOUND",
        "statusCode": 404,
        "requestId": "req_abc123"
      }
    }
  ]
}
```

**Standard error codes:**

| Code | HTTP Status | Meaning |
|------|------------|---------|
| `UNAUTHENTICATED` | 401 | Missing or invalid JWT |
| `FORBIDDEN` | 403 | Insufficient permissions (RBAC) |
| `NOT_FOUND` | 404 | Resource not found |
| `VALIDATION_ERROR` | 400 | Input validation failed |
| `CONFLICT` | 409 | Resource already exists (e.g., duplicate email) |
| `RATE_LIMITED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Unexpected server error |

### 2.3 Pagination (Relay Connection Spec)

All list queries use cursor-based pagination following the Relay Connection specification:

```graphql
type SomeConnection {
  edges: [SomeEdge!]!
  pageInfo: PageInfo!
  totalCount: Int!
}

type SomeEdge {
  cursor: String!
  node: SomeType!
}

type PageInfo {
  hasNextPage: Boolean!
  hasPreviousPage: Boolean!
  startCursor: String
  endCursor: String
}
```

**Query parameters:**
- `first: Int` — Number of items to return (default: 20, max: 100)
- `after: String` — Opaque cursor for the next page

Cursors are base64-encoded representations of the item's sort key (typically `created_at` timestamp + `id`).

### 2.4 Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Query fields | `camelCase` | `creatorAnalytics`, `unreadNotificationCount` |
| Mutation fields | `camelCase` | `createPost`, `sendMessage` |
| Types | `PascalCase` | `User`, `CollaborationParticipant` |
| Input types | `PascalCase` + `Input` suffix | `RegisterInput`, `CreatePostInput` |
| Enum values | `SCREAMING_SNAKE_CASE` | `IN_PROGRESS`, `COLLABORATION_INVITE` |
| Connection types | `PascalCase` + `Connection` suffix | `PostConnection` |

## 3. REST Conventions

### 3.1 URL Structure

```
/api/v1/{resource}
/api/v1/{resource}/{id}
/api/v1/{resource}/{id}/{sub-resource}
```

**Examples:**
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/users/{id}`
- `GET /health`

### 3.2 Response Envelope

**Success (single resource):**
```json
{
  "data": { "id": "...", "email": "..." }
}
```

**Success (list):**
```json
{
  "data": [...],
  "meta": {
    "page": 1,
    "perPage": 20,
    "total": 150,
    "totalPages": 8
  }
}
```

**Error:**
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Password too weak",
    "details": {
      "errors": ["Must contain at least one uppercase letter"]
    }
  }
}
```

### 3.3 HTTP Methods

| Method | Usage |
|--------|-------|
| `GET` | Read resource(s) |
| `POST` | Create resource |
| `PUT` | Full update |
| `PATCH` | Partial update |
| `DELETE` | Delete (soft-delete via `PATCH` in most cases) |

## 4. Authentication

### 4.1 Token Format

All authenticated requests must include a JWT in the `Authorization` header:

```
Authorization: Bearer <access_token>
```

### 4.2 Token Lifecycle

| Token | TTL | Rotation |
|-------|-----|----------|
| Access Token | 15 minutes | Rotated on refresh |
| Refresh Token | 7 days | Rotated on each use |

### 4.3 GraphQL Auth

GraphQL queries/mutations use the same `Authorization: Bearer <token>` header.
The auth middleware extracts the user before the resolver runs.

**Public queries (no auth required):**
- Trending sounds, creator discovery, public profiles

**Authenticated queries:**
- Everything else — enforced via `Permissions` in resolvers.

## 5. Rate Limiting

| Tier | Limit | Window |
|------|-------|--------|
| Unauthenticated | 30 requests | 60 seconds |
| Authenticated (User) | 100 requests | 60 seconds |
| Authenticated (Creator) | 300 requests | 60 seconds |
| Authenticated (Admin) | 1000 requests | 60 seconds |

Rate limit headers in REST responses:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1690000000
```

### Active protection

The active middleware retains a global limit of **60 HTTP requests per IP per
60 seconds** (configurable via `rate_limit_per_minute`). Health endpoints are exempt.
The tier limits above are not yet implemented.

REST authentication request values are sent as JSON bodies. Registration uses
`email`, `username`, and `password`; login uses `email` and `password`; refresh
uses `refresh_token`. Credential-bearing query parameters are rejected. Access
tokens continue to be returned in the existing JSON response and supplied to
protected routes in the `Authorization: Bearer` header.

REST authentication endpoints also have independent IP-based sliding-window
buckets:

| Endpoint | Limit |
|----------|-------|
| `POST /auth/login` | 10 attempts per 60 seconds |
| `POST /auth/register` | 5 requests per 60 minutes |
| `POST /auth/refresh` | 30 requests per 60 seconds |
| `POST /auth/logout` | 30 requests per 60 seconds |

Login requests are charged before authentication, so failed credential attempts
are counted. Every auth request continues to count against the global IP limit.

The active media upload endpoint (`POST /media/posts/{post_id}`) also has an
independent limit of **10 upload requests per user (or anonymous client IP) per
60 minutes**, plus a cumulative volume limit of **1 GiB per user/IP per 60
minutes**. The volume budget is reserved before storage is called; rejected
uploads do not execute the storage operation. These rate limits do not change
the existing 8 MiB image or 512 MiB video file-size validation.

GraphQL queries and mutations are rejected before resolver execution when they
exceed either **10 selected-field levels of depth** or **1,000 selected fields**
per request. Root fields count as depth 1. Aliases and fields expanded from
fragments count as selected fields, and `@skip`/`@include` directives are
evaluated using coerced variable values. When JSON batching is enabled, the field
count is aggregated across the request's operations.
Limit violations use the regular GraphQL `errors` response (HTTP 200) with a
`QUERY_DEPTH_LIMIT_EXCEEDED` or `QUERY_FIELD_LIMIT_EXCEEDED` code and a
`statusCode` of 400 in `extensions`.

GraphQL HTTP mutations also have independent sliding-window action buckets:

| Mutation action | Limit per 60 seconds |
|-----------------|----------------------|
| `register` / `login` (shared bucket) | 5 |
| `createPost` | 5 |
| `createComment` / `addComment` (shared bucket) | 20 |
| `sharePost` | 10 |
| `follow` | 20 |
| `createCollaboration` (proposal/request creation) | 5 |
| `sendMessage` | 30 |
| `startLiveStream` | 2 |

Action buckets use the verified authenticated user ID, or the direct client IP for
anonymous/invalid-token requests. Changing access tokens or IPs does not reset a
user's bucket; different users on the same IP have separate action buckets but
still share global IP protection. Forwarded IP headers are not trusted.

Only the selected operation is charged. Executable root mutation fields are
counted separately by action, including aliases and fragments, honoring
`@skip`/`@include` and coerced variable defaults. Repeated selections merged by
GraphQL count once. All action costs are reserved before any resolver runs;
an over-budget request executes no mutation fields and consumes no action quota.
Accepted attempts consume quota even when a resolver subsequently fails.
JSON array batching is disabled by Strawberry's default schema configuration;
the limiter also aggregates costs if batching is enabled in the future.

The authenticated streaming REST API has additional user-ID-based limits:

| Endpoint / resource | Limit |
|---------------------|-------|
| `POST /api/streams` | 2 start attempts per 60 seconds AND 10 per 60 minutes |
| Pending or active `StreamSession` records | 1 per user |
| `POST /api/streams/{stream_id}/stop` | 10 owned stop requests per 60 seconds |
| `POST /api/streams/{stream_id}/viewers/join` | 20 authenticated attempts per 60 seconds |
| `POST /api/streams/{stream_id}/viewers/{viewer_session_id}/heartbeat` | 30 authenticated attempts per 60 seconds |
| `POST /api/streams/{stream_id}/viewers/{viewer_session_id}/leave` | 20 authenticated attempts per 60 seconds |
| `POST /api/streams/{stream_id}/subscriptions` | 20 authenticated attempts per 60 seconds |

The minute start limit follows the existing GraphQL `startLiveStream` threshold.
The hourly start budget, concurrent-session cap, and stop budget are conservative
initial REST policies. Start attempts consume both budgets before session
creation, including attempts that fail process startup or encounter an occupied
slot. Over-budget attempts do not consume additional action quota. Stopping does
not reset either start budget, preventing repeated stop/start churn. Owned
idempotent stop requests count too; authentication, validation, and ownership
failures retain their existing responses.

A database partial unique index reserves the slot when the pending session is
flushed, before FFmpeg starts. This prevents simultaneous starts across workers
from bypassing the cap. Pending sessions count as live reservations; ended/failed
sessions do not. Successful stop, startup failure, and persisted process exit
release the slot through the existing status transitions. A concurrent-limit
429 advises retrying after 60 seconds, but expiry alone does not release the
slot: the previous session must end or fail.

Deploy Alembic revision `199` before serving this policy on an existing database.
If an owner already has multiple pending/active sessions, the index creation
fails rather than silently stopping streams or rewriting session history.
Resolve those sessions via the existing lifecycle before retrying the migration.

Rate limit violations return HTTP 429 with the existing
`{"error":{"code":"TOO_MANY_REQUESTS","message":"Too many requests. Please slow down and try again later."}}`
body and an integer `Retry-After` header in seconds. Action limits are process-local,
like the global middleware; multiple workers/replicas do not share quota. Distributed
frequency-limit storage, WebSocket mutation limits, and other Week 5 limits remain
follow-up work. The streaming concurrent-session cap is database-enforced and is
shared across workers/replicas.

### Stream-attributed follow action

The existing GraphQL `follow(username, streamSessionId)` mutation uses the
existing `follow` quota (20/user/60 seconds), including no-op retries and aliases.
Rate-limit rejection is HTTP 429 with the standard body and `Retry-After`, before
any mutation executes. No streaming REST engagement endpoint is added.

Attribution requires an active, started, unfinalized `StreamSession` owned by the
named creator and an authenticated caller with an unclosed, unexpired viewer
lease on that exact stream. Missing streams, wrong owners, inactive lifecycle,
and missing participation use the existing GraphQL error envelope with
`NOT_FOUND` (404), `FORBIDDEN` (403), `CONFLICT` (409), and `FORBIDDEN` (403),
respectively. Authentication and self-follow restrictions are unchanged.
Only newly inserted canonical follow relationships emit attributed signals;
retries cannot move an existing follow to another stream. No attribution is
inferred from time, posts, or external-platform actions.

Deploy pending migrations through `206` before enabling this reporting.
See [creator analytics](./CREATOR_ANALYTICS.md#native-stream-follows).

### Native stream subscription action

After deploying revision `205`, `POST /api/streams/{stream_id}/subscriptions`
accepts `{ "creator_id": "<UUID>" }`. It requires the existing active-user
authentication dependency. The subscriber is always the authenticated user;
extra fields such as `user_id`, client timestamps, and platform identifiers
are rejected. The supplied recipient must own the persisted stream (403 on
mismatch); missing streams return 404 and self-subscriptions return 403.

Success returns HTTP 200 with `{ id, stream_id, user_id, creator_id, created_at }`.
A repeated action by the same user on the same stream returns the original
record. Database uniqueness and conflict-safe insertion enforce idempotency
across workers. Retry attempts consume the existing streaming action quota.
Archived streams are eligible; viewer presence and external destination accounts
are not required. This endpoint records a native action, not a paid entitlement.
See [creator analytics](./CREATOR_ANALYTICS.md#native-stream-subscriptions).

### Authenticated viewer presence

Deploy the existing Alembic revision `202` before enabling these collection
routes. They use canonical `StreamSession` IDs, not the legacy GraphQL live-stream
IDs. No new migration or real-time transport is required.

- Join accepts only `{"client_session_id": "<UUID>"}`. Identity comes exclusively
  from authentication. The parent must be active with a valid server start time
  and no end time. Successful first joins and retries return HTTP 200 with the
  same viewer-session identity for the same broadcast/user/client attempt.
  Retries do not extend the lease, reset timestamps, or reopen closed/expired
  intervals. A reconnect after expiry/leave needs a new client attempt UUID.
- Heartbeat and leave address the returned `viewer_session_id`. They accept no
  body or `{}`; extra body fields are rejected with HTTP 422. Both verify the
  authenticated viewer and supplied stream; a missing, foreign, or mismatched
  viewer session returns HTTP 404, including when the caller is the creator.
- Heartbeat renews only an unclosed session whose join time has arrived and whose
  deadline is strictly in the future, while the parent remains active. Rejected
  renewal or inactive join returns HTTP 409. Renewal never shortens a deadline.
- Leave preserves the first finalized end time. It can be used after expiry or
  broadcast termination and selects the earliest of server time, lease expiry,
  and the broadcast's end timestamp. It never reopens an interval.

Responses contain `viewer_session_id`, `stream_id`, `client_session_id`,
`joined_at`, `lease_expires_at`, `left_at`, and point-in-time `is_active`.
Presence requires an unclosed interval, `joined_at <= server_now`, an unexpired
lease, and an active canonical parent; an unclosed expired row is not active.
No user identity, creator metrics, credentials, or input-source URLs are exposed.

`STREAMING_VIEWER_LEASE_SECONDS` configures the server lease (default 60 seconds,
positive integer, maximum 3600). With the default, clients should renew roughly
every 20 seconds while playback is present, rather than for every playback tick.
Each action has one authenticated-user bucket across all broadcasts, tabs,
devices, tokens, and IP changes. Validated attempts, including idempotent retries,
authorization misses, and conflicts, consume their action budget before database
work. Invalid bodies are rejected before the action service. All requests still
pass through the existing global per-IP middleware (default 60 requests/minute);
combined traffic may therefore hit its limit first. IP is never viewer identity.

PostgreSQL parent/viewer row locks and conditional updates serialize presence
changes against termination and prevent renewal of closed/expired intervals.
The stop path acquires its final parent lock only after the process-exit callback
finishes, avoiding a callback deadlock and retaining the callback's end time.
Persistence failures use the standard logged error response, not a successful
presence response. Expired intervals need no background cleanup to be inactive;
this slice does not bulk-finalize viewer rows on termination.

## 6. Timestamps

- All timestamps use **ISO 8601 UTC** format: `2026-07-08T19:00:00Z`
- Timestamps are always in UTC (no local timezone offsets)
- Database columns use `TIMESTAMPTZ`
- API responses use string serialization

## 7. IDs

- All primary keys are **UUID v7** (time-sortable)
- IDs are serialized as lowercase UUID strings in API responses
- Example: `"0190-acbd-7e00-0000-8f3a2b1c4d5e"`

## 8. CORS

Allowed origins are configured via `ALLOWED_ORIGINS` environment variable:

| Environment | Origins |
|-------------|---------|
| Development | `http://localhost:3000`, `http://localhost:19006` (React Native) |
| Staging | `https://staging.connextionz.com` |
| Production | `https://connextionz.com`, `capacitor://localhost`, `ionic://localhost` |

## 9. Versioning

- **GraphQL:** No versioning — the schema is the contract; deprecated fields are marked `@deprecated`
- **REST:** URL-based versioning (`/api/v1/`, `/api/v2/`)
- Breaking changes trigger a major version bump

## 10. Caching

| Layer | TTL | Strategy |
|-------|-----|----------|
| CDN (CloudFront) | 1 hour | Cache-Control: public, max-age=3600 for static assets |
| API Cache (Redis) | 5 minutes | Cache based on query hash; invalidated on mutation |
| Client Cache (Apollo) | Configurable | Normalized cache with field-level policies |

Cache-Control headers for REST:
```
Cache-Control: private, max-age=300
```

## 11. WebSocket / Subscriptions

### 11.1 Connection

```
ws://localhost:8000/api/graphql  (WebSocket for GraphQL subscriptions)
wss://api.connextionz.com/api/graphql  (Production)
```

### 11.2 Authentication

WebSocket connections pass the JWT as a connection parameter:
```json
{
  "connectionParams": {
    "Authorization": "Bearer <access_token>"
  }
}
```

### 11.3 Subscription Protocol

Uses `graphql-ws` protocol (not `subscriptions-transport-ws`).

## 12. File Uploads

File uploads use **pre-signed S3 URLs**:
1. Client requests an upload URL via `getUploadUrl` query
2. Client uploads directly to S3 via the pre-signed URL
3. Client submits the S3 key with the GraphQL mutation

This avoids streaming large files through the API server.

## 13. Health Checks

| Endpoint | Purpose | Kubernetes Probe |
|----------|---------|-----------------|
| `GET /health` | Basic liveness | `livenessProbe` |
| `GET /health/ready` | Dependency readiness (DB, Redis, MQ) | `readinessProbe` |
| `GET /health/live` | Application alive | `livenessProbe` |

## 14. Content Security & Input Validation

- All GraphQL inputs are validated by Strawberry type system
- String fields have length limits enforced at the resolver level
- File uploads have size limits (8 MiB maximum for images, 512 MiB maximum for video)
- User-generated HTML is sanitized before storage
- SQL injection is prevented by SQLAlchemy parameterized queries
