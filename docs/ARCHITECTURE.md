# ConnextionZ Platform Architecture

## System Design Overview

The ConnextionZ platform is engineered as a **Modular Monolith** that can evolve into microservices when scale demands. The architecture prioritizes:
- **Scale**: Horizontal scaling capabilities
- **Modularity**: Clean separation of concerns
- **Simplicity**: Easy local development
- **User Experience**: Responsive and accessible interfaces

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Client Layer                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Web App    │  │ Mobile App   │  │   Admin UI   │   │
│  │   (React)    │  │ (React Native│  │              │   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │ GraphQL / REST
┌─────────────────────────────────────────────────────────────┐
│                     API Gateway Layer                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │            FastAPI (Python)                          │  │
│  │  - Authentication & Authorization                    │  │
│  │  - Request Validation                                │  │
│  │  - Rate Limiting                                     │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                   Business Logic Layer                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │   Features  │  │  Services   │  │   AI/ML     │     │
│  │  - Auth     │  │  - RabbitMQ │  │  - Two-Tower│     │
│  │  - Feed     │  │  - Redis    │  │  - Agentic  │     │
│  │  - Collab   │  │  - LLM API  │  │    Router   │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                     Data Access Layer                        │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         Repositories (Python)                         │  │
│  │  - PostgreSQL (Primary DB)                           │  │
│  │  - pgvector (Vector Storage)                         │  │
│  │  - Redis (Cache & Sessions)                          │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                     Infrastructure Layer                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │     AWS      │  │ LocalStack   │  │   Docker     │   │
│  │  - EKS       │  │ (Local Dev)  │  │  Compose     │   │
│  │  - RDS       │  │              │  │              │   │
│  │  - ElastiCache│ │              │  │              │   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Component Details

### 1. Client Layer
- **Web Application**: React with TypeScript
  - Responsive design with mobile-first approach
  - GraphQL client (Apollo Client or Relay)
  - Real-time updates via WebSocket

- **Mobile Application**: React Native with TypeScript
  - Shared business logic with web via `/hooks` and `/utils`
  - Native performance for critical paths
  - Offline-first architecture

### 2. API Gateway Layer (FastAPI)
- **Authentication**: JWT-based with refresh tokens
- **Authorization**: Role-based access control (RBAC)
- **Validation**: Pydantic models for request/response validation
- **Documentation**: OpenAPI (Swagger) auto-generated
- **Rate Limiting**: Per-user and per-endpoint limits
- **CORS**: Configured for web and mobile origins

### 3. Business Logic Layer

#### Features Module
Domain-driven design with feature modules:
- **Auth**: Registration, login, password reset, 2FA
- **Feed**: Personalized content ranking using Two-Tower model
- **Collaboration**: Request management, matching algorithm
- **Messaging**: Real-time chat with WebSocket support
- **Streaming**: Live stream management and playback
- **Analytics**: Metrics aggregation and reporting

#### Services Module
External integrations:
- **RabbitMQ**: Asynchronous task processing
  - Email notifications
  - Content moderation
  - Analytics event processing
  
- **Redis**: Caching and session management
  - User sessions
  - API response cache
  - Real-time presence tracking
  
- **LLM API**: Agentic router for complex queries
  - Intent parsing
  - Natural language search
  - Content summarization

#### AI/ML Module
- **Two-Tower Model**: User and Item embedding generation
  - User Tower: User preferences, behavior, demographics
  - Item Tower: Content features, metadata
  - Cosine similarity for matching
  
- **Agentic Router**: LLM-powered query understanding
  - Parses natural language searches
  - Routes to appropriate retrieval mechanism
  - Falls back to Two-Tower for simple queries

- **Candidate Generation**: Approximate Nearest Neighbor (ANN)
  - Indexes user and item embeddings
  - Fast retrieval of top-K candidates
  - Supports pgvector or dedicated vector DB

### 4. Data Access Layer (Repositories)
Clean abstraction over database operations:
- **UserRepository**: User profiles, authentication, reputation
- **ContentRepository**: Posts, comments, media metadata
- **CollaborationRepository**: Collaboration requests, agreements
- **FeedRepository**: Feed items, interactions, embeddings
- **AnalyticsRepository**: Event logs, aggregated metrics

### 5. Infrastructure Layer

#### AWS Production Environment
- **EKS**: Kubernetes for container orchestration
- **RDS**: PostgreSQL with pgvector extension
- **ElastiCache**: Redis for caching
- **MQ**: RabbitMQ for message queuing
- **S3**: Media storage
- **CloudFront**: CDN for static assets

#### Local Development Environment
- **LocalStack**: AWS service emulation
- **Docker Compose**: Full stack orchestration
- **Infracost**: Cost estimation for Terraform plans
- **Checkov**: Security scanning for IaC

## Paid Algorithm Specification

**Status: campaign foundation, targeting/frequency eligibility, read-only ranking/
soft pacing, campaign-aware pagination, dedicated discovery UI, internal
authoritative impression accounting, and the Paid engagement attribution
boundary implemented; public Paid delivery disabled.**
Campaign persistence, owner-only management, the Paid delivery ledger, bounded
candidate ranking, isolated discovery UI, and Paid/non-Paid engagement
separation exist. Billing and public Paid enablement do not.

### Current algorithm architecture

The four independently addressable algorithm areas are Organic (relevance and
discovery), Viral (momentum), Community (relationship/community relevance), and
Paid (controlled paid distribution). A shared API selector is not a mixed feed.

| Area | Current implementation | Boundary |
|------|------------------------|----------|
| Organic | `FeedAlgorithm.ORGANIC` -> `_organic_feed` -> `_for_you_feed` in [backend/api/graphql.py](../backend/api/graphql.py) | Reuses existing personalized candidate generation and deterministic relevance/engagement ranking in [feed_ranking.py](../backend/repositories/feed_ranking.py). |
| Viral | `FeedAlgorithm.VIRAL` -> `_viral_feed` in [backend/api/graphql.py](../backend/api/graphql.py); `score_viral_post` in [feed_ranking.py](../backend/repositories/feed_ranking.py) | Recent timestamp-windowed engagement momentum, not lifetime popularity or payment. |
| Community | `FeedAlgorithm.COMMUNITY` -> `_community_feed` in [backend/api/graphql.py](../backend/api/graphql.py); `score_community_post` in [feed_ranking.py](../backend/repositories/feed_ranking.py) | Follow/mutual-follow relationships and creator interaction affinity. |
| Paid | `FeedAlgorithm.PAID` -> `_paid_feed` in [backend/api/graphql.py](../backend/api/graphql.py) | Paginates ranked candidates and has a receipt-backed `PaidDeliveryService` serialization path behind a hard-coded disabled gate; requests still raise `NotImplementedError("Paid feed delivery is disabled")`, with no fallback. |

`FeedFilter.algorithm` and `_feed` already dispatch all four areas. Without an
algorithm, `_feed` retains For You or Following behavior. Explicit Organic
currently shares the For You implementation; independence means separate
addressability and policy, not duplicating code unnecessarily.

The production feed path above is repository-based deterministic ranking.
The Two-Tower/ANN feed diagram below is a high-level target design, not a
description of this resolver's current execution path.

### Product and discovery boundary

- Creators may pay to promote their own eligible content. Brands/businesses
  may pay to promote eligible content they are authorized to promote. The
  advertiser differs; both use the same Paid campaign eligibility contract.
- Paid optimizes qualified distribution/reach within campaign constraints.
  Payment does not guarantee engagement, likes, comments, follows, purchases,
  or organic virality.
- Paid results belong exclusively to a **dedicated Paid discovery area**,
  eventually requested through the existing `PAID` selector. Do not splice
  them into Organic, Viral, Community, For You, or Following candidate pools,
  ranked pages, quotas, or fallback results.
- A promoted post may independently qualify for a non-Paid area through its
  organic behavior and that area's normal safety/relevance requirements.
  Neither Paid status nor paid-origin engagement automatically qualifies or
  boosts it there. Do not globally exclude the underlying post merely because
  it has a campaign.
- Keep the current explicit unimplemented error until a separately approved
  implementation exists. A request for Paid must not silently return Organic
  results or claim successful campaign delivery.

### Authoritative campaign relationship

The initial review found no campaign/promotion backend model. The foundation
now uses [PaidCampaign](../backend/app/models/paid_campaign.py), registered with
the existing model package, and migration
[207_paid_campaign_foundation.py](../backend/alembic/versions/207_paid_campaign_foundation.py)
after revision 206. It references `users.id` and `posts.id`; `Post` has no
manually editable Paid/sponsored flag. Imported advertising specifications and
frontend sponsored brand-deal copy are not competing delivery implementations.

The future delivery candidate is a **campaign plus promoted content**, not a
permanently classified post. `PaidCampaign` provides the following foundation:

| Concept | Required responsibility |
|---------|-------------------------|
| Campaign identity | Stable ID for delivery, attribution, and sponsored representation. |
| Advertiser | Creator or brand/business identity, including promotion authorization; distinct from the content owner. |
| Promoted post/content | Relationship to existing eligible content; campaign lifecycle must not own post lifecycle. |
| Status | `DRAFT`, `SCHEDULED`, `ACTIVE`, `PAUSED`, `EXHAUSTED`, `COMPLETED`, or `CANCELLED`. |
| `start_at`, `end_at` | Delivery window, evaluated against authoritative server time. |
| Budget | Positive `budget_minor_units` integer and explicit uppercase three-letter `currency`; no conversion, charging, or currency registry is implemented. |
| Spending/delivery state | Read-only-to-owners `spent_minor_units`, `impressions_delivered`, and `reach_delivered` counters start at zero. The internal ledger atomically consumes impressions/reach; monetary charging, reservations, and pacing are not implemented. |
| Maximum impressions | Campaign-wide impression delivery ceiling. |
| Maximum reach | Campaign-wide unique-audience ceiling, not another impression count. |
| Frequency cap | Per-viewer repeat-delivery ceiling with an explicitly defined time window. |
| Target audience | Validated campaign audience requirements and exclusions. |
| Delivery metrics | Separately attributed impressions, unique reach, views, watch behavior, and secondary engagement. |
| `created_at`, `updated_at` | Audit timestamps; reuse existing timestamp conventions where appropriate. |

UUIDv7 and timestamp mixins follow existing conventions. Constraints enforce a
valid time window, positive budget/optional limits, nonnegative spend/delivery,
spend and counters within configured ceilings, reach no greater than impressions,
and a paired positive frequency cap/window. Indexes cover owner/creation,
promoted post, and status/time-window candidate selection. API/DB integers are
bounded to positive signed 32-bit configuration values. Missing impression/reach
limits mean no ceiling from that field; budget and end time are always required.

Any active user may act as an advertiser, including a creator or brand/business
using an existing user identity. The confirmed foundation scope permits only
promotion of that user's own approved, published, public posts from a public
profile (or an account without a profile). Promotion of another creator's posts
requires future explicit permission support; admin status does not bypass
campaign ownership. Multiple campaigns per post are allowed as separate
candidates, not merged deliveries; ranking selection policy is deferred.
Internal selection IDs deduplicate individual deliveries as described below.

`frequency_cap` pairs with `frequency_window_seconds`. Targeting uses the
existing nullable JSONB column with a strict, bounded `tags` contract described
below. Frequency eligibility uses a read-only impression-history boundary;
the production history provider counts only recorded server selections and
fails closed if campaign history is missing or inconsistent. Neither check
implements public delivery or a generic targeting engine.

### Lifecycle and eligibility contract

| State | Meaning | Deliverable? |
|-------|---------|--------------|
| `DRAFT` | Campaign configuration not activated. | No |
| `SCHEDULED` | Configured for a future start; activation must be validated. | No |
| `ACTIVE` | Authorized for delivery subject to all current constraints. | Only if every gate passes |
| `PAUSED` | Delivery suspended. | No |
| `EXHAUSTED` | Budget or a campaign-wide delivery ceiling consumed. | No |
| `COMPLETED` | Delivery window ended or campaign finished. | No |
| `CANCELLED` | Campaign cancelled. | No |

Owner-managed transitions are explicit: draft -> scheduled/active/cancelled;
scheduled -> active/paused/completed/cancelled; active ->
paused/exhausted/completed/cancelled; paused ->
scheduled/active/exhausted/completed/cancelled. Exhausted, completed, and
cancelled are terminal. Repeating the current status is idempotent but still
validates applicable gates. Configuration can be replaced only in draft/paused
states, cannot lower limits below consumed capacity, and cannot change currency
after spending or delivery. Ownership/post identity/counters are not API inputs.

Scheduling requires a future start; activation requires current campaign-level
eligibility and safe content. Exhausted/completed transitions require actual
exhaustion/expiry. There is no background scheduler: a scheduled campaign does
not automatically become active when time passes. Future orchestration must
reconcile ended/exhausted campaigns. Owner updates lock the campaign row.
Delivery must check current constraints even when stored status is stale:

- Do not deliver before `start_at` or once `end_at` is reached.
- Do not deliver any non-`ACTIVE` campaign, including paused/cancelled ones.
- Reject exhausted budget, reached maximum impressions, or reached maximum
  unique reach.
- Reject the current viewer when frequency limits or targeting prevent delivery.
- Reject unsafe, unavailable, deleted, unpublished, unapproved, or invisible
  content, including applicable account, privacy, block, and mute restrictions.
  Advertiser eligibility and promotion authorization must also remain valid.

Frequency/targeting/visibility rejection for one viewer is not automatically
campaign-wide exhaustion. Ending or exhausting a campaign ends only Paid
delivery: it must not delete, archive, hide, or otherwise remove the post.

Revalidate at delivery time, including when resuming a cached/snapshot page.
Future accounting must atomically reserve/consume capacity, deduplicate
retries, and prevent concurrent requests from exceeding budget/caps. Best-effort
analytics cannot serve as the authoritative spending or cap ledger.

### Initial Paid targeting

[paid_targeting.py](../backend/services/paid_targeting.py) defines the supported
Paid targeting contract and a pure, deterministic matcher. The agreed initial
scope is existing onboarding topics matched to the viewer's existing
`Profile.tags`, not inferred interests or demographic data.

```json
{"tags": ["music", "tech"]}
```

- The only supported field is `tags`. Approved topic values are `music`,
  `fitness`, `travel`, `cooking`, `art`, `tech`, `gaming`, `fashion`, and
  `business`, drawn from the existing
  [onboarding topics](../src/app/components/Onboarding.tsx).
  The Paid allowlist is a restriction on existing tags, not a new user/topic
  registry or taxonomy. No new category table is created.
- `null` or `{}` means no audience restriction. If present, `tags` must be
  a nonempty JSON array of 1-9 strings, each at most 64 characters.
  Validation trims/case-folds and stores sorted lowercase values, rejecting
  duplicates after normalization. Unknown topics, unsupported fields, malformed
  values, and empty arrays are rejected on creation/configuration updates.
- Multiple tags use **OR**: at least one approved viewer profile tag must match.
  All other campaign, safety, and frequency conditions remain **AND** gates.
  This slice supports one dimension only; no hidden multi-dimension semantics.
- Profile tags use the same trim/case-fold comparison. Missing/deleted profiles,
  missing/empty tags, or no overlapping approved tags fail restricted targeting;
  they do not fail broad targeting. Non-topic values in existing profile data
  cannot establish a match.
- The matcher does not infer tags from watched/liked content, use post hashtags
  as viewer interests, or read location, health, ethnicity/race, religion,
  sexual orientation, age, or other demographic/sensitive attributes. Fields
  such as `categories` or `interests` are not aliases for `tags`.
- Stored legacy arbitrary targeting is not silently treated as broad targeting:
  activation and eligibility reject unsupported configurations. Owners must
  replace them through the existing draft/paused configuration workflow.

No GraphQL schema change is required: the existing owner-only JSON input/output
is now validated by the service. Validated configuration fits well within the
former 16 KiB foundation limit; arbitrary targeting JSON is no longer accepted.

### Read-only frequency eligibility

[PaidFrequencyService](../backend/services/paid_frequency.py) evaluates a
per-viewer, per-campaign rolling cap. With a cap, it asks
[PaidImpressionHistoryReader](../backend/repositories/paid_impression_history_repository.py)
for a count of distinct, server-validated Paid deliveries in the UTC window
`[now - frequency_window_seconds, now]`. Both bounds are included, so a prior
delivery at the evaluation instant counts; future timestamps are excluded. A count below the cap
allows frequency eligibility; at or above it denies eligibility.

Without a cap/window, frequency passes without querying history. This does not
permit delivery or bypass any other gate. Invalid cap/window pairs or invalid
counts raise explicit validation errors. There are four result states:
`ALLOWED`, `CAPPED`, `ATTRIBUTION_UNAVAILABLE`, and `NOT_CHECKED`; the last is
used when lifecycle/targeting already fails, avoiding an unnecessary history
lookup. Unknown history is never zero history.

The current repository reads the authoritative `paid_deliveries` ledger.
It compares lifetime recorded impressions and distinct viewers with campaign
counters before returning a window count. Missing schema/campaign or mismatched
counters raise `PaidImpressionHistoryUnavailable`. The frequency service returns an explicit
ineligible result with the reason and logs `paid_frequency.attribution_unavailable`.
Unexpected database/provider failures propagate. Arbitrary views, likes,
post counters, ordinary `VIDEO_IMPRESSION` events, or metadata claiming
`source: PAID` do not establish reliable Paid impression history.

`PaidCampaignService.evaluate_eligibility` exposes lifecycle/cap, targeting,
and frequency results without mutations. It is not a post-safety/delivery
authorization API. Candidate retrieval first applies existing campaign/content/
account/public-profile and block/mute gates, loads viewer tags once if needed,
then returns only candidates passing targeting and frequency.
The initial SQL pool remains bounded at 100; post-filtering can yield fewer
candidates. Paid pagination traverses this bounded pool, not every campaign in
the database.

These eligibility reads do not record an impression, change spend/reach/counters,
or create analytics events. Ranking is a separate read-only step after every gate.
`_paid_feed` still raises its explicit
delivery-disabled error even if candidates pass. Capped campaigns with complete
history and no prior impressions can now pass frequency; incomplete legacy
history does not become zero history.

### Authoritative impression accounting (gated)

[PaidDelivery](../backend/app/models/paid_delivery.py) and backend migration
[208](../backend/alembic/versions/208_paid_delivery_accounting.py) establish
the smallest campaign-specific selection/impression ledger, not a replacement
general analytics pipeline. GraphQL `_paid_feed` calls
[PaidDeliveryService](../backend/services/paid_delivery_service.py) after
campaign-aware pagination, but only behind the unchanged hard-coded disabled
gate; normal feeds do not invoke Paid delivery.

The gated trusted server selector:

1. Obtain a `PaidCandidate`, then call `select_candidate` to persist a fresh
   server-generated UUID selection bound to campaign, post, and viewer.
   This checks all current eligibility/safety gates; pending selections consume
   no capacity and count as neither impressions nor frequency history.
2. Call `record_impression(viewer, delivery_id)` for that selected item.
   Only a persisted selection for the authenticated viewer supplies attribution;
   arbitrary post IDs, campaign IDs, sponsored flags, or analytics metadata cannot.
   Pending selections revalidate campaign/time/budget/caps, post/account/profile,
   block/mute, targeting, and frequency before accounting.
3. Commit the encompassing transaction **before** exposing the sponsored result.
   The returned internal receipt contains delivery/post/campaign IDs, `source=PAID`,
   `is_sponsored=true`, and `sponsored_label=Sponsored`, never budget or targeting.

An impression represents one server-accounted serving of a selected item, not
proof of a visible pixel, watch, engagement, or a billable event. The existing
normal feed uses returned-page impressions but has no stable delivery identity;
Paid therefore uses its own server-generated selection ID. Retrying or
acknowledging the same ID returns its recorded receipt without new counts,
even after campaign expiry/exhaustion. A legitimate later serving needs a
fresh server selection ID. There is no arbitrary deduplication time window.
Selecting again is not a retry mechanism; future transport/page retries must
reuse persisted IDs. No public selector or acknowledgement endpoint exists.

[PaidDeliveryRepository](../backend/repositories/paid_delivery_repository.py)
requires PostgreSQL. It acquires `FOR UPDATE` on the campaign, refreshing stale
ORM state, then re-reads the selection after waiting. This same parent lock
serializes all campaign accounting and owner-management updates. Database
`clock_timestamp()` is obtained after locking, not at request start.
Under the lock, lifetime history completeness is checked even for uncapped
campaigns, frequency is checked, prior recorded rows establish whether this
viewer is new, and SQL expressions increment impressions by one and reach
only for a first viewer. The row's `impressed_at` is stamped in the same
transaction. Nested savepoints roll back partial writes on errors; repositories
never commit. An outer rollback discards the entire accounting operation.
Concurrent retries, last-capacity requests, and first-viewer requests cannot
double-count or overshoot. Production uses the existing READ COMMITTED
transaction convention; unexpected database/serialization failures propagate,
not success-shaped fallbacks.

Reach is campaign-lifetime distinct viewer UUIDs, not impressions or rolling
frequency. Reaching maximum reach blocks all further delivery, including
repeats, preserving the campaign eligibility contract. Viewer UUIDs have no
deletion-cascading FK, so deleting a viewer does not erase accounting history
or invalidate surviving campaign counters. Campaign/post hard deletion
cascades their ledger; campaign expiry does not delete posts or history.
Retention/anonymization must preserve counter/history consistency in future
privacy and archival policies.

Budget availability is enforced but spend is **unchanged**: CPI/pricing,
monetary charging, spend reservations, and payment handling are undefined.
Delivery counters remain server-controlled and absent from management inputs.
Selection expiry/cleanup, server transport delivery retry
mapping, and crash/response recovery belong to the future delivery layer;
pending selections are always revalidated and do not reserve capacity.

### Paid ranking contract

[PaidRankingService](../backend/services/paid_ranking.py) orders only the campaigns
returned by the existing campaign/content/account/safety, targeting, and frequency
pipeline. `PaidCampaignService.get_ranked_candidates` shares the same filtering
as `get_eligible_candidates`; scoring never overrides or substitutes for a gate.

For each eligible campaign, use exact rational arithmetic:

- `elapsed = clamp((now - start_at) / (end_at - start_at), 0, 1)`.
- `consumed = max(impressions_delivered / max_impressions,
  reach_delivered / max_reach)`, omitting unconfigured caps.
- Primary score: `deficit = elapsed - consumed`, descending. Behind-pace
  campaigns therefore outrank ahead-of-pace campaigns.
- Secondary score: `remaining = 1 - consumed`, descending, then fewer lifetime
  impressions, then campaign UUID ascending as the stable final tie-breaker.

This is **soft pacing**, not a delivery reservation or hard per-time ceiling.
An ahead-of-pace campaign remains selectable if nothing ranks ahead of it.
Authoritative counter updates cause its priority to fall on subsequent reads,
sharing opportunity among eligible campaigns rather than always serving the
oldest campaign first. Schedule edits/resumption use the current configured
window; there is no separate active-time clock.

When neither delivery cap is configured, no finite delivery target is inferred:
the pacing deficit is neutral (`0`), remaining fraction is `1`, and fewer
impressions balances otherwise equal campaigns. Budget/spend remains a hard
eligibility gate only; no price, currency conversion, or budget-to-impression
mapping is invented. No priority field, engagement, demographic, behavioral,
Organic, Viral, or Community signals are used, and no migration is needed.

The candidate pool remains bounded at 100 before viewer filtering/ranking.
Campaign-aware pagination is read-only and adds no public sponsored output.
Ranking writes no selections, impressions, spend, reach, or analytics; the
existing authoritative delivery service still revalidates and records delivery.
`_paid_feed` invokes candidate pagination but retains its explicit delivery-disabled error.

### Paid pagination contract

`PaidCampaignService.get_candidate_page` ranks the full existing bounded eligible
pool once, independently of page size (1–100). It retains the first/highest-ranked
campaign for each promoted post, so neither campaigns nor posts repeat across
pages. The existing exact-rational pacing order and final ascending campaign UUID
tie-breaker are unchanged.

[paid_pagination.py](../backend/services/paid_pagination.py) provides a versioned
`paid1.` cursor containing the ordered campaign/post identities and last-served
campaign UUID. It is HMAC-signed with the existing application signing secret,
domain-separated from authentication, and bound to the viewer. Invalid, tampered,
oversized, wrong-viewer, duplicate-identity, and non-Paid cursors raise explicit
errors. It is not a delivery token or an eligibility assertion. Key rotation
invalidates outstanding cursors; start a fresh pagination traversal.

Continuation requests do not rerank: they read snapshot campaigns through the
same server-side campaign/content/account/profile/safety, current schedule,
budget/impression/reach, targeting, and frequency gates. Only identities still
matching their stored campaign/post relationship are returned, in snapshot order
after the anchor, even if the anchor itself is now ineligible. New campaigns do
not displace the snapshot; pacing clock changes do not reorder it. A fresh first
page evaluates current ranking again. Retries preserve ordering while eligibility
is unchanged; removed/ineligible candidates are omitted without placeholders or
non-Paid fallback. Empty/exhausted pages have no items and no next cursor.

Pagination creates no selections or impressions and changes no spend, reach,
delivery counters, frequency history, or analytics. Actual selection/accounting
remains exclusively in `PaidDeliveryService`, with authoritative revalidation.
The GraphQL Paid handler contains a receipt-backed delivery integration after
the campaign-aware page is prepared. Each candidate must receive a server
selection and authoritative impression receipt before serialization; accounting
is committed before the page is returned and rolled back on failures. Its
hard-coded public delivery gate remains disabled and still raises
`Paid feed delivery is disabled`. This integration does not add billing, spend
calculation, or a client retry token; the existing server selection identity
remains the accounting deduplication key.
Organic, Viral, Community, For You, and Following use their unchanged feed paths.

### Dedicated Paid discovery UI

[PaidDiscovery.tsx](../src/app/PaidDiscovery.tsx) is a separate local screen in
[App.tsx](../src/app/App.tsx), reached through the Discover header and returning
to Discover through Back. It consumes the existing viewer-scoped `usePaidFeed`
without changing normal feed queries, stores, or presentation.

The page reuses the shared page shell, empty-state, avatar, and theme primitives.
Its small read-only Paid card renders server-provided creator, caption, media,
and sponsored labeling, with a Paid fallback label when metadata is absent.
Native video controls do not autoplay or invoke normal-feed watch/engagement
accounting. Loading, empty, errors, delivery-disabled unavailability, manual
retry, and Load More are distinct states. Cursor handling and append
deduplication remain in the existing Paid store; the UI performs neither.
No automatic retry loop or normal-feed fallback exists. Public Paid delivery
remains disabled; this UI does not expose campaign management or billing.
Focused rendering/navigation tests run with `npm run test:paid-ui`, using
the existing Vite JSX compiler and Node test runner without new dependencies.

### Paid frontend/backend contract validation

[test_paid_frontend_contract.py](../backend/tests/test_paid_frontend_contract.py)
validates the shipped `PAID_FEED_QUERY` against the real GraphQL schema and executes
it through the real Paid candidate service with the existing isolated SQLite
fixture. `feed(cursor: String, limit: Int!, filter: {algorithm: PAID})` exposes
`items: [FeedItemType!]!` and `nextCursor: String`; item fields include post and
creator data, `isSponsored: Boolean!`, `sponsoredLabel: String`, and
`paidCampaignId: UUID` (a JSON string or null).

The public field still fails with `data: null`, error path `["feed"]`, message
`Paid feed delivery is disabled`, and extensions `code: NOT_IMPLEMENTED`,
`statusCode: 501`. The test passes that actual serialized result to the existing
Node frontend client/store, verifying an explicit error with no fallback items
and no continuation request. It also checks that the actual service-generated
signed cursor is forwarded unchanged and accepted on backend continuation.
Internal candidate pages are not public delivery responses: successful public
Paid post serialization cannot be exercised while the gate is disabled.
Existing Paid UI tests verify this exact error becomes Paid-unavailable, and
focused pagination tests verify append deduplication preserves server order.
No new cross-runtime test framework or delivery enablement is introduced.

### Future integration and reuse

Use the existing backend modular-monolith layout and algorithm dispatch.
Do not introduce a second algorithm enum/router or adopt the prototype feed.

| Surface | Existing reuse point | Future work and boundary |
|---------|---------------------|--------------------------|
| Posts/content | [Post and Media](../backend/app/models/content.py), [PostRepository](../backend/repositories/content_repository.py) | [PaidCampaignRepository](../backend/repositories/paid_campaign_repository.py) selects campaign-first candidates with content/account/public-profile safety gates. Normal discovery pools are not a Paid eligibility source. |
| Advertiser | [User and Profile](../backend/app/models/user.py), user/profile repositories | [PaidCampaignService](../backend/services/paid_campaign_service.py) enforces active accounts and own-post promotion. Separate business identities/delegation are deferred. |
| Categories | `Post.tags`, `Profile.tags`, `PostRepository.get_interest_pool`, `AnalyticsRepository.user_interest_tags` | Existing topics are tags, not a canonical content-category taxonomy. Define any category-to-tag mapping or taxonomy deliberately; reputation badge/endorsement categories are not content categories. |
| Targeting | Existing `Profile.tags` and onboarding topics | Strict approved-topic validation and pure matching in [paid_targeting.py](../backend/services/paid_targeting.py). No behavioral or sensitive-attribute targeting. |
| Recommendations | `_paid_feed`, [PaidCampaignService](../backend/services/paid_campaign_service.py), [paid_ranking.py](../backend/services/paid_ranking.py) | Paid paginates a bounded eligible pool with deterministic soft pacing and campaign-aware snapshots, but still raises the explicit delivery-disabled error. No delivery, counter updates, or impression events run; non-Paid ranking is untouched. |
| Safety | `_feed_item_is_visible`, `FeedSafetyRepository`, profile/follow lookup | Reuse current visibility rules and soft-delete-aware reads; add campaign/advertiser eligibility. Payment never bypasses moderation or privacy. |
| API representation | `FeedItemType`, `FeedPageType`, `_post_to_feed_item` in [graphql.py](../backend/api/graphql.py) | Output-only `is_sponsored`, `sponsored_label`, and `paid_campaign_id` default to false/null/null for existing feeds. Service `PaidCandidate` derives sponsored identity from campaign retrieval; no Paid item is returned yet. |
| Analytics | [AnalyticsEvent / InteractionSignal](../backend/app/models/analytics.py), [AnalyticsEventService](../backend/services/analytics_event_service.py), existing analytics repositories/services | Resolve provenance from impressed Paid delivery records, retain campaign/delivery IDs, and exclude attributed signals/counters from non-Paid recommendation inputs. No competing event pipeline. |

Sponsored identification belongs to the returned delivery context. Users must
see a clear sponsored label for Paid delivery. A manually editable
`is_sponsored` field cannot be the sole source of truth. A post returned
independently in another algorithm must not be marked as a Paid delivery just
because it has an active campaign.

Owner-only GraphQL management contracts live in
[paid_campaigns.py](../backend/api/paid_campaigns.py):
`createPaidCampaign(input)` always creates a draft;
`paidCampaign(id)` retrieves only the caller's campaign;
`updatePaidCampaign(id, input)` changes status and/or replaces the complete
configuration. Omitted optional fields within replacement configuration are
cleared; omitted configuration leaves it unchanged. `AppContext.require_auth`
and the service's active-account check reuse existing authentication. Missing
or unowned IDs return the same not-found error. Only these protected management
outputs contain budget, spend, and targeting; feed outputs do not.

### Paid engagement attribution boundary

Post-associated Paid attribution is resolved on the server from the exact
impressed delivery ID returned with a Paid feed item, and validated against the
authenticated viewer and post. The stored delivery and campaign IDs—not
client-supplied campaign/source/sponsored flags or a post's active campaign
status—establish Paid provenance. No delivery ID means no Paid attribution;
the server does not infer it from an earlier Paid exposure to the same post.
This covers post-associated events, recommendation signals, and active
like/save/share/watch rows; creator-level follows and purchases have no post
delivery context in the current model.

[Analytics Event Tracking](./ANALYTICS_EVENTS.md) provides the centralized
recording boundary. Migration
[209](../backend/alembic/versions/209_paid_engagement_attribution.py) adds
nullable delivery/campaign attribution to analytics and post-interaction
records without relabeling historical rows. Paid events remain available in
event/signal analytics, including campaign-filtered queries.

Creator affinity, viewer history, For You engagement rates, Viral momentum,
interest tags, and For You counter/tie-break inputs exclude Paid-attributed
activity. Historical null-attribution rows remain classified as non-Paid;
ordinary returned-page impressions are not automatically Paid or billable.
Preserve actual like/save/follow state; attribution is not a reason to undo
user actions.

The interaction API accepts the opaque delivery identity as a reference only;
all attributed writes revalidate it against the ledger. Repeated and
cross-surface exposure therefore do not make an ordinary non-Paid interaction
Paid by recency alone. Do not enable public Paid delivery until migration 209
is deployed and this attribution behavior has been validated against the
production PostgreSQL transaction path.

### Conflicts, competing implementations, and activation prerequisites

- [src/recommendation](../src/recommendation/) is a separate prototype with
  `RankingModel`, empty candidate-pool generation, and 70/20/10 composition.
  It is not imported by the inspected backend feed path and does not implement
  the four-area campaign contract. Do not build Paid there or duplicate its
  scoring/composition in production.
- Root [repositories](../repositories/__init__.py) is a compatibility shim
  pointing at `backend/repositories`, not another ranking implementation.
  [creator_scoring.py](../backend/repositories/creator_scoring.py) supports
  creator discovery, not an alternative Paid post-delivery strategy.
- The shared selector/handler architecture already fits Paid. The existing
  unimplemented handler is deliberate, not a fallback to fill with ads.
- Current attribution and denormalized metrics cannot safely support Paid
  activation unchanged. Paid now has separate campaign-aware pagination with
  current eligibility revalidation; post-only non-Paid cursors cannot substitute
  for it or establish delivery authorization.
- Migration configuration is also split: [backend/alembic.ini](../backend/alembic.ini)
  selects the PostgreSQL backend chain (now head 208), whereas root
  [alembic.ini](../alembic.ini) selects a separate legacy tree with competing
  heads. Deploy this foundation through the backend configuration, not the
  root legacy migration tree. No legacy migration files are changed here.
- Remaining future components include delegated advertiser authorization,
  background lifecycle orchestration, hard pacing/pricing and atomic monetary
  spending, publicly delivered campaign-aware sponsored output,
  and attributed engagement/reporting. Authoritative
  impressions, lifetime reach, retry deduplication, and frequency history are
  implemented internally; public delivery remains gated. Payments and billing
  remain separately out of scope.

Future acceptance checks must cover independent dispatch/no mixed feeds; both
advertiser types; every lifecycle/time/budget/impression/reach/frequency/
targeting/safety gate; concurrent deliveries and retry deduplication; sponsored
labels derived from campaigns; campaign expiry leaving posts intact;
independent organic qualification with no Paid boost; and isolation of paid
analytics from all non-Paid ranking inputs. Until then, Paid remains unavailable.

Focused foundation tests are in
[test_paid_campaigns.py](../backend/tests/test_paid_campaigns.py), including
SQLite persistence/constraints using copied model metadata, owner-only GraphQL,
deterministic lifecycle/eligibility, candidate isolation/safety, and PostgreSQL
migration upgrade/downgrade DDL generation. Targeting/frequency cases cover
approved/unknown/sensitive topic validation, profile matching, API enforcement,
rolling-window requests, below/at/above-cap counts, unavailable attribution,
combined gates, and read-only candidate filtering. Existing feed/ranking regression
tests preserve normal algorithm behavior.
[test_paid_ranking.py](../backend/tests/test_paid_ranking.py) covers deterministic
ordering, stable ties, remaining delivery opportunity, soft pacing, uncapped
balancing, eligibility-before-ranking, and absence of accounting mutations.
[test_paid_pagination.py](../backend/tests/test_paid_pagination.py) covers ranked
snapshot traversal/retries, UUID ties, duplicate posts, current eligibility
revalidation, disappearing anchors, exhausted pages, cursor validation, bounded
pool drift, feed isolation, the disabled public handler, and zero accounting writes.
[test_paid_delivery_postgres.py](../backend/tests/test_paid_delivery_postgres.py)
opts in with `PAID_TEST_DATABASE_URL` against an isolated PostgreSQL database;
each test uses a unique schema. It exercises server attribution, forged/wrong
identity rejection, post/viewer revalidation, authoritative windows, missing
history, real parallel transactions for caps/reach/frequency/idempotency,
savepoint/outer rollback, and migration 208 upgrade/downgrade against PostgreSQL.
The migration must be applied through
the project's deployment workflow; foundation tests do not migrate a shared DB.

**PAID ALGORITHM ARCHITECTURE: READY** (campaign foundation, not delivery-ready).

## Data Flow Examples

### 1. User Feed Generation
```
User Request → API Gateway → Feed Service → Two-Tower Model
                                            ↓
                                    Generate User Embedding
                                            ↓
                                    ANN Search (pgvector)
                                            ↓
                                    Retrieve Candidate Items
                                            ↓
                                    Re-ranking (ML Model)
                                            ↓
                                    Apply Business Rules
                                            ↓
                                    Return Personalized Feed
```

### 2. Collaboration Request
```
Creator A sends request → API Gateway → Collaboration Service
                                            ↓
                                    Validate Request
                                            ↓
                                    Publish to RabbitMQ
                                            ↓
                                    Notification Service (Consumer)
                                            ↓
                                    Push Notification to Creator B
                                            ↓
                                    Real-time Update via WebSocket
```

### 3. Natural Language Search
```
User Query → API Gateway → Agentic Router (LLM)
                            ↓
                    Parse Intent & Entities
                            ↓
                    Route to Search Strategy
                            ↓
                    [Simple] → Two-Tower Retrieval
                    [Complex] → Hybrid Search (Vector + Keyword)
                            ↓
                    Merge & Rank Results
                            ↓
                    Return Relevant Creators/Content
```

## Security Architecture

### Authentication Flow
1. User login with email/password
2. Server validates credentials
3. Returns access token (short-lived) and refresh token (long-lived)
4. Client stores tokens securely
5. Access token used for API requests
6. Automatic token refresh via refresh token

### Data Protection
- **Encryption at Rest**: PostgreSQL, Redis, S3
- **Encryption in Transit**: TLS 1.3 for all communications
- **Sensitive Data**: Hashed passwords (bcrypt), encrypted PII
- **Secrets Management**: AWS Secrets Manager or HashiCorp Vault

### API Security
- Input validation on all endpoints
- SQL injection prevention (parameterized queries)
- XSS protection (Content Security Policy)
- CSRF protection for web clients
- Rate limiting to prevent abuse

## Scalability Considerations

### Horizontal Scaling
- **Stateless API Servers**: Multiple FastAPI instances behind load balancer
- **Database Read Replicas**: Offload read queries
- **Redis Cluster**: Distributed caching
- **RabbitMQ Clustering**: High availability for message queue

### Performance Optimization
- **Database Indexing**: Optimized queries for common access patterns
- **CDN**: Static asset delivery via CloudFront
- **Lazy Loading**: Images and non-critical content
- **Pagination**: Limit data transfer for large collections
- **Background Jobs**: Offload heavy computations to workers

## Monitoring & Observability

### Metrics
- **Application Metrics**: Request latency, error rates, throughput
- **Business Metrics**: User engagement, collaboration success rate
- **Infrastructure Metrics**: CPU, memory, disk usage

### Logging
- Structured logging (JSON format)
- Centralized log aggregation (ELK stack or AWS CloudWatch)
- Correlation IDs for request tracing

### Alerting
- PagerDuty integration for critical issues
- Slack notifications for warnings
- Automated rollback on deployment failures

## Disaster Recovery

### Backup Strategy
- **PostgreSQL**: Daily automated backups to S3
- **Redis**: AOF persistence with periodic snapshots
- **Media Assets**: S3 cross-region replication

### Recovery Procedures
- RTO (Recovery Time Objective): < 1 hour
- RPO (Recovery Point Objective): < 15 minutes
- Documented runbooks for common failure scenarios
- Regular disaster recovery drills

## Future Evolution: Microservices Transition

The Modular Monolith design enables gradual extraction of microservices:
1. **Phase 1**: Extract Collaboration Service (high isolation, clear boundaries)
2. **Phase 2**: Extract Feed Service (ML-heavy, different scaling needs)
3. **Phase 3**: Extract Messaging Service (real-time requirements)
4. **Phase 4**: Extract Analytics Service (write-heavy, eventual consistency)

Each transition will be guided by:
- Team autonomy requirements
- Independent scaling needs
- Data ownership clarity
- Deployment frequency

## References
- SRS Section 4: Full Stack Backend & Data Tier
- SRS Section 5: AI & Recommendation Engine
- SRS Section 6: Frontend & Mobile Requirements
- SRS Section 7: Non-Functional Requirements
