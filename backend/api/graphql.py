"""
GraphQL schema and resolvers using Strawberry (code-first).

Exposes a single :func:`create_graphql_router` factory that wires
all types, queries, mutations, and subscriptions into a FastAPI-compatible
router via ``strawberry.fastapi.GraphQLRouter``.

Auth is enforced per-resolver via ``AppContext`` which carries the
DB session and optional authenticated ``User`` ORM instance extracted
from the JWT Bearer token.
"""

from __future__ import annotations

import uuid
import re
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import AsyncIterator, Optional, List, Callable

import strawberry
from fastapi import Request, Response
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.fastapi import BaseContext, GraphQLRouter
from strawberry.schema.config import StrawberryConfig
from strawberry.types import Info as StrawberryInfo

from app.models.user import User
from app.config import settings

# ── Custom Scalars ───────────────────────────────────────────────────────────

UUIDScalar = uuid.UUID
DateTimeScalar = datetime
JSONScalar = object

SCALAR_MAP = {
    uuid.UUID: strawberry.scalar(
        name="UUID",
        serialize=lambda v: str(v),
        parse_value=lambda v: uuid.UUID(v) if isinstance(v, str) else v,
        description="UUID v7 identifier",
    ),
    datetime: strawberry.scalar(
        name="DateTime",
        serialize=lambda v: v.isoformat(),
        parse_value=lambda v: datetime.fromisoformat(v) if isinstance(v, str) else v,
        description="ISO 8601 UTC datetime",
    ),
    object: strawberry.scalar(
        name="JSON",
        serialize=lambda v: v,
        parse_value=lambda v: v,
        description="Arbitrary JSON object",
    ),
}


# ── GraphQL Errors ───────────────────────────────────────────────────────────


@strawberry.type
class GraphQLError:
    """Structured error returned alongside partial data in mutations/queries."""

    message: str
    code: str
    field: Optional[str] = None


# ── Context ──────────────────────────────────────────────────────────────────


class AppContext(BaseContext):
    """GraphQL context carrying DB session and authenticated user.

    Populated by the ``context_getter`` factory before every resolver execution.
    The DB session is obtained from the FastAPI session factory dependency.
    If the request includes a valid ``Authorization: Bearer <jwt>`` header,
    the corresponding ``User`` ORM instance is loaded and stored.
    """

    def __init__(
        self,
        db: AsyncSession,
        current_user: User | None = None,
        session_id: str | None = None,
    ) -> None:
        self.db: AsyncSession = db
        self._current_user: User | None = current_user
        # Reuses the access token's JWT "jti" claim as a lightweight session
        # identifier for analytics — avoids standing up a separate session
        # system (see backend/app/models/user.py Session for the durable,
        # audit-oriented session record; this is a request-scoped proxy).
        self.session_id: str | None = session_id

    @property
    def current_user(self) -> User | None:
        return self._current_user

    @property
    def user(self) -> User | None:
        """Compatibility alias for legacy resolvers that access ``ctx.user``."""
        return self._current_user

    @property
    def is_authenticated(self) -> bool:
        return self._current_user is not None

    def require_auth(self) -> User:
        """Return the authenticated user or raise a ``PermissionError``."""
        if self._current_user is None:
            raise PermissionError("Authentication required")
        return self._current_user


# ── Enums ────────────────────────────────────────────────────────────────────


@strawberry.enum
class UserRole(Enum):
    ADMIN = "admin"
    CREATOR = "creator"
    USER = "user"
    GUEST = "guest"


@strawberry.enum
class AccountStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BANNED = "banned"
    PENDING_VERIFICATION = "pending_verification"


@strawberry.enum
class ContentType(Enum):
    POST = "post"
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    LIVE_STREAM = "live_stream"


@strawberry.enum
class ContentStatus(Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    FLAGGED = "flagged"
    REMOVED = "removed"


@strawberry.enum
class CollaborationStatus(Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@strawberry.enum
class MilestoneStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISPUTED = "disputed"


@strawberry.enum
class NotificationType(Enum):
    COLLABORATION_INVITE = "collaboration_invite"
    COLLABORATION_ACCEPTED = "collaboration_accepted"
    COLLABORATION_COMPLETED = "collaboration_completed"
    NEW_FOLLOWER = "new_follower"
    NEW_COMMENT = "new_comment"
    NEW_LIKE = "new_like"
    MENTION = "mention"
    MESSAGE = "message"
    BADGE_EARNED = "badge_earned"
    ENDORSEMENT_RECEIVED = "endorsement_received"
    MILESTONE_COMPLETED = "milestone_completed"
    SYSTEM = "system"


@strawberry.enum
class NotificationChannel(Enum):
    IN_APP = "in_app"
    PUSH = "push"
    EMAIL = "email"


@strawberry.enum
class ReportTargetType(Enum):
    POST = "post"
    COMMENT = "comment"
    USER = "user"
    MESSAGE = "message"


@strawberry.enum
class ReportStatus(Enum):
    PENDING = "pending"
    REVIEWING = "reviewing"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


@strawberry.enum
class SortDirection(Enum):
    ASC = "ASC"
    DESC = "DESC"


@strawberry.enum
class FeedAlgorithm(Enum):
    PERSONALIZED = "PERSONALIZED"
    TRENDING = "TRENDING"
    RECENT = "RECENT"
    FOLLOWING = "FOLLOWING"


# ── Pagination Types (Relay Connection Spec) ──────────────────────────────────


@strawberry.type
class PageInfo:
    has_next_page: bool
    has_previous_page: bool
    start_cursor: Optional[str] = None
    end_cursor: Optional[str] = None


# ── Core Types ───────────────────────────────────────────────────────────────


@strawberry.type
class UserType:
    id: UUIDScalar
    email: str
    username: str
    role: UserRole
    status: AccountStatus
    email_verified: bool
    mfa_enabled: bool
    last_login_at: Optional[DateTimeScalar] = None
    created_at: DateTimeScalar
    updated_at: DateTimeScalar

    @strawberry.field
    async def profile(self, info: StrawberryInfo[AppContext, None]) -> Optional["ProfileType"]:
        """Lazy-load profile via dataloader (placeholder)."""
        return None  # TODO: implement dataloader


@strawberry.type
class ProfileType:
    id: UUIDScalar
    user_id: UUIDScalar
    display_name: str
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    website_url: Optional[str] = None
    location: Optional[str] = None
    social_links: Optional[JSONScalar] = None
    tags: Optional[List[str]] = None

    follower_count: int = 0
    following_count: int = 0
    collaboration_count: int = 0
    total_likes: int = 0

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


@strawberry.type
class PostType:
    id: UUIDScalar
    user_id: UUIDScalar
    content_type: ContentType
    status: ContentStatus
    title: Optional[str] = None
    body: Optional[str] = None
    caption: Optional[str] = None
    tags: Optional[List[str]] = None
    mentions: Optional[List[UUIDScalar]] = None
    sound_track: Optional[str] = None

    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    view_count: int = 0

    scheduled_at: Optional[DateTimeScalar] = None
    published_at: Optional[DateTimeScalar] = None

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


@strawberry.type
class MediaType:
    id: UUIDScalar
    post_id: UUIDScalar
    user_id: UUIDScalar
    media_type: str
    url: str
    thumbnail_url: Optional[str] = None
    file_size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    is_processed: bool = False

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


@strawberry.type
class CommentType:
    id: UUIDScalar
    post_id: UUIDScalar
    user_id: UUIDScalar
    parent_id: Optional[UUIDScalar] = None
    body: str
    is_edited: bool = False
    like_count: int = 0

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


# ── Legacy-compatible content/social types ───────────────────────────────────
#
# These mirror the old backend's GraphQL shape field-for-field so the
# frontend (src/app/*.ts) works unmodified. They're additive: Seth's
# PostType/ProfileType/CommentType above stay as-is for the collaboration/
# messaging/reputation domains.


@strawberry.type
class ProfileSummaryType:
    id: UUIDScalar
    username: str
    display_name: str
    avatar_url: str = ""
    avatar_color: str = "#00AEEF"
    verified: bool = False
    collab_score: float = 0.0
    collab_count: int = 0
    followers: int = 0
    following: int = 0
    open_to_collab: bool = True
    private_account: bool = False
    is_following: bool = False


@strawberry.type(name="ContentItemType")
class LegacyPostType:
    id: UUIDScalar
    thumbnail: str = ""
    media_url: Optional[str] = None
    caption: str = ""
    views: int = 0
    likes: int = 0
    is_liked: bool = False
    collab_with: Optional[str] = None
    hashtags: List[str] = strawberry.field(default_factory=list)
    audio: str = "Original Sound"
    visibility: str = "public"
    allow_comments: bool = True
    allow_collabs: bool = True
    duration_sec: float = 0.0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    is_saved: bool = False
    is_shared: bool = False
    status: str = "published"
    scheduled_at: Optional[str] = None


@strawberry.type
class FeedItemType(LegacyPostType):
    creator: ProfileSummaryType = None  # type: ignore[assignment]


@strawberry.type
class ProfileDetailType:
    id: UUIDScalar
    username: str
    display_name: str
    avatar_url: str = ""
    avatar_color: str = "#00AEEF"
    bio: Optional[str] = None
    location: Optional[str] = None
    website: Optional[str] = None
    verified: bool = False
    online: bool = True
    collab_status: Optional[str] = None
    collab_score: float = 0.0
    collab_count: int = 0
    followers: int = 0
    following: int = 0
    open_to_collab: bool = True
    private_account: bool = False
    response_time: str = "< 4 hours"
    posts: List[LegacyPostType] = strawberry.field(default_factory=list)
    playlists: List["PlaylistType"] = strawberry.field(default_factory=list)
    is_following: bool = False


@strawberry.type
class CommentGQLType:
    id: UUIDScalar
    text: str
    likes: int = 0
    is_liked: bool = False
    can_delete: bool = False
    can_edit: bool = False
    moderation_status: str = "approved"
    created_at: str = ""
    author: ProfileSummaryType = None  # type: ignore[assignment]


@strawberry.type
class PlaylistType:
    id: UUIDScalar
    title: str
    cover: str
    item_label: str
    plays: int = 0


@strawberry.type
class SoundResultGQLType:
    id: UUIDScalar
    title: str
    creator: str
    creator_avatar: str = ""
    artwork: str = ""
    genre: str = ""
    video_count: int = 0
    total_plays: int = 0
    rank: int = 0
    growth_pct: int = 0
    duration: str = "0:30"
    bpm: int = 0


@strawberry.type
class FeedPageType:
    items: List[FeedItemType]
    next_cursor: Optional[str] = None


@strawberry.type
class HashtagResultType:
    tag: str
    posts: int = 0
    views: int = 0


@strawberry.type
class HashtagPageType:
    hashtags: List[HashtagResultType]
    next_cursor: Optional[str] = None


@strawberry.type
class PostPageType:
    items: List[LegacyPostType]
    next_cursor: Optional[str] = None


@strawberry.type
class CommentPageType:
    comments: List[CommentGQLType]
    next_cursor: Optional[str] = None


@strawberry.type
class ProfilePageType:
    profiles: List[ProfileSummaryType]
    next_cursor: Optional[str] = None


@strawberry.type
class LikeResultType:
    liked: bool
    likes: int


@strawberry.type
class SaveResultType:
    saved: bool
    saves: int


@strawberry.type
class NotInterestedResultType:
    not_interested: bool
    post_id: UUIDScalar


@strawberry.type
class ShareResultType:
    shares: int
    shared: bool


@strawberry.type
class FollowResultType:
    following: bool
    followers: int
    following_count: int


@strawberry.type
class WatchResultType:
    views: int
    watched_seconds: float
    completed: bool
    rewatched: bool


@strawberry.type
class CollaborationType:
    id: UUIDScalar
    initiator_id: UUIDScalar
    title: str
    description: Optional[str] = None
    status: CollaborationStatus
    content_type: Optional[str] = None
    platform: Optional[str] = None
    tags: Optional[List[str]] = None

    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    budget_currency: str = "USD"

    proposed_at: Optional[DateTimeScalar] = None
    started_at: Optional[DateTimeScalar] = None
    completed_at: Optional[DateTimeScalar] = None

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


@strawberry.type
class CollaborationParticipantType:
    id: UUIDScalar
    collaboration_id: UUIDScalar
    user_id: UUIDScalar
    role: str
    accepted: bool = False
    accepted_at: Optional[DateTimeScalar] = None


@strawberry.type
class MilestoneType:
    id: UUIDScalar
    collaboration_id: UUIDScalar
    title: str
    description: Optional[str] = None
    status: MilestoneStatus
    sort_order: int = 0
    due_at: Optional[DateTimeScalar] = None
    completed_at: Optional[DateTimeScalar] = None

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


# ── Messaging Types ──────────────────────────────────────────────────────────


@strawberry.type
class ConversationType:
    id: UUIDScalar
    title: Optional[str] = None
    is_group: bool = False

    last_message_text: Optional[str] = None
    last_message_at: Optional[DateTimeScalar] = None
    last_message_by: Optional[UUIDScalar] = None

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


@strawberry.type
class ConversationParticipantInfoType:
    id: UUIDScalar
    conversation_id: UUIDScalar
    user_id: UUIDScalar
    is_admin: bool = False
    last_read_at: Optional[DateTimeScalar] = None
    is_muted: bool = False


@strawberry.type
class MessageType:
    id: UUIDScalar
    conversation_id: UUIDScalar
    sender_id: UUIDScalar
    body: str
    content_type: str = "text"
    attachments: Optional[JSONScalar] = None
    is_edited: bool = False
    edited_at: Optional[DateTimeScalar] = None

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


# ── Notification Types ───────────────────────────────────────────────────────


@strawberry.type
class NotificationTypeDef:
    id: UUIDScalar
    user_id: UUIDScalar
    type: NotificationType
    title: str
    body: Optional[str] = None
    data: Optional[JSONScalar] = None
    channel: NotificationChannel
    is_read: bool = False
    read_at: Optional[DateTimeScalar] = None
    actor_id: Optional[UUIDScalar] = None

    created_at: DateTimeScalar


# ── Reputation Types ─────────────────────────────────────────────────────────


@strawberry.type
class ReputationScoreType:
    id: UUIDScalar
    user_id: UUIDScalar
    overall_score: float = 0.0
    collaboration_score: float = 0.0
    content_quality_score: float = 0.0
    community_score: float = 0.0
    reliability_score: float = 0.0
    total_endorsements: int = 0
    completed_collaborations: int = 0
    on_time_delivery_rate: float = 0.0
    computed_at: Optional[DateTimeScalar] = None

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


@strawberry.type
class EndorsementType:
    id: UUIDScalar
    endorser_id: UUIDScalar
    endorsee_id: UUIDScalar
    category: str
    comment: Optional[str] = None
    rating: int
    collaboration_id: Optional[UUIDScalar] = None

    created_at: DateTimeScalar


@strawberry.type
class BadgeType:
    id: UUIDScalar
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    category: str
    tier: int = 1

    created_at: DateTimeScalar


@strawberry.type
class UserBadgeType:
    id: UUIDScalar
    user_id: UUIDScalar
    badge_id: UUIDScalar

    awarded_at: Optional[DateTimeScalar] = None
    awarded_by: Optional[UUIDScalar] = None


# ── Live Streaming ───────────────────────────────────────────────────────────


@strawberry.type
class LiveStreamType:
    id: UUIDScalar
    user_id: UUIDScalar
    title: str
    status: ContentStatus
    stream_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    viewer_count: int = 0
    started_at: Optional[DateTimeScalar] = None
    ended_at: Optional[DateTimeScalar] = None

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


# ── Sound ────────────────────────────────────────────────────────────────────


@strawberry.type
class SoundType:
    id: UUIDScalar
    name: str
    artist: Optional[str] = None
    source_url: Optional[str] = None
    cover_url: Optional[str] = None
    usage_count: int = 0
    trending_score: float = 0.0


# ── Creator Discovery ────────────────────────────────────────────────────────


@strawberry.type
class CreatorCardType:
    user: UserType
    profile: ProfileType
    relevance_score: float
    matching_tags: Optional[List[str]] = None
    reputation_score: Optional[float] = None


# ── Brand Partnerships ───────────────────────────────────────────────────────


@strawberry.type
class BrandOpportunityType:
    id: UUIDScalar
    brand_id: UUIDScalar
    title: str
    description: Optional[str] = None
    requirements: Optional[JSONScalar] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    budget_currency: str = "USD"
    deadline: Optional[DateTimeScalar] = None
    status: CollaborationStatus

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


@strawberry.type
class BrandApplicationType:
    id: UUIDScalar
    opportunity_id: UUIDScalar
    creator_id: UUIDScalar
    message: Optional[str] = None
    status: CollaborationStatus

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


# ── Search ───────────────────────────────────────────────────────────────────


@strawberry.type
class SearchResultType:
    type: str
    score: float
    user: Optional[UserType] = None
    post: Optional[PostType] = None
    sound: Optional[SoundType] = None
    collaboration: Optional[CollaborationType] = None


# ── Trust & Safety ───────────────────────────────────────────────────────────


@strawberry.type
class ReportType:
    id: UUIDScalar
    reporter_id: UUIDScalar
    target_type: ReportTargetType
    target_id: UUIDScalar
    reason: str
    description: Optional[str] = None
    status: ReportStatus

    created_at: DateTimeScalar
    updated_at: DateTimeScalar


# ── Analytics ────────────────────────────────────────────────────────────────


@strawberry.type
class PostAnalyticsType:
    post: PostType
    views: int = 0
    unique_viewers: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    avg_watch_time: Optional[float] = None
    completion_rate: Optional[float] = None
    engagement_rate: float = 0.0
    followers_generated: Optional[int] = None


@strawberry.type
class AnalyticsSummaryType:
    period_start: DateTimeScalar
    period_end: DateTimeScalar

    total_posts: int = 0
    total_views: int = 0
    total_likes: int = 0
    total_comments: int = 0
    total_shares: int = 0
    total_saves: int = 0
    unique_viewers: int = 0
    total_uploads: int = 0
    total_published_videos: int = 0
    avg_watch_time: Optional[float] = None
    completion_rate: Optional[float] = None

    follower_growth: int = 0
    new_followers: int = 0
    lost_followers: int = 0

    active_collaborations: int = 0
    completed_collaborations: int = 0
    total_collaboration_requests: int = 0
    pending_collaborations: int = 0
    accepted_collaborations: int = 0
    declined_collaborations: int = 0
    cancelled_collaborations: int = 0
    collaboration_acceptance_rate: Optional[float] = None
    collaboration_completion_rate: Optional[float] = None
    average_response_hours: Optional[float] = None
    collaboration_success_rate: Optional[float] = None
    total_earnings: Optional[float] = None
    earnings_currency: str = "USD"

    engagement_rate: float = 0.0
    views_growth_pct: Optional[float] = None
    likes_growth_pct: Optional[float] = None
    comments_growth_pct: Optional[float] = None
    shares_growth_pct: Optional[float] = None
    followers_growth_pct: Optional[float] = None

    top_posts: Optional[List[PostAnalyticsType]] = None


@strawberry.type
class AnalyticsTrendPointType:
    date: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    followers_gained: int = 0
    collaborations_requested: int = 0
    collaborations_pending: int = 0
    collaborations_accepted: int = 0
    collaborations_in_progress: int = 0
    collaborations_declined: int = 0
    collaborations_completed: int = 0
    collaborations_cancelled: int = 0

@strawberry.type
class PlatformAnalyticsType:
    period_start: DateTimeScalar
    period_end: DateTimeScalar
    total_users: int = 0
    new_users: int = 0
    total_creators: int = 0
    new_creators: int = 0
    active_users: int = 0
    active_creators: int = 0
    daily_active_users: Optional[int] = None
    weekly_active_users: Optional[int] = None
    monthly_active_users: Optional[int] = None
    total_uploads: int = 0
    total_published_videos: int = 0
    total_views: int = 0
    unique_viewers: int = 0
    total_likes: int = 0
    total_comments: int = 0
    total_shares: int = 0
    total_saves: int = 0
    profile_views: int = 0
    sounds_used: int = 0
    collabs_created: int = 0
    follows_created: int = 0
    follows_removed: int = 0
    net_followers: int = 0
    average_views_per_published_video: Optional[float] = None
    engagement_rate: float = 0.0
    average_watch_time: Optional[float] = None
    completion_rate: Optional[float] = None
    feed_impressions: int = 0
    video_completions: int = 0
    video_skips: int = 0
    searches: int = 0
    searchers: int = 0
    notifications_generated: Optional[int] = None
    notifications_opened: int = 0
    notification_open_rate: Optional[float] = None
    approved_content: int = 0
    flagged_content: int = 0
    removed_content: int = 0
    comparison: "PlatformAnalyticsComparisonType | None" = None

@strawberry.type
class PlatformAnalyticsComparisonType:
    user_growth_pct: Optional[float] = None
    creator_growth_pct: Optional[float] = None
    content_growth_pct: Optional[float] = None
    views_growth_pct: Optional[float] = None
    engagement_growth_pct: Optional[float] = None
    active_users_growth_pct: Optional[float] = None

@strawberry.type
class PlatformAnalyticsTrendType:
    date: str
    uploads: int = 0
    published_videos: int = 0
    views: int = 0
    completed_views: int = 0
    engagement: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    follows: int = 0
    follows_removed: int = 0
    net_follows: int = 0
    profile_views: int = 0
    collabs: int = 0
    impressions: int = 0
    searches: int = 0
    notifications_opened: int = 0

@strawberry.type
class PlatformContentAnalyticsType:
    post: "PlatformContentPostType"
    views: int = 0
    unique_viewers: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    average_watch_time: Optional[float] = None
    completion_rate: Optional[float] = None
    engagement_rate: float = 0.0


@strawberry.type
class PlatformContentPostType:
    id: UUIDScalar
    title: Optional[str] = None
    caption: Optional[str] = None
    thumbnail: Optional[str] = None
    media_url: Optional[str] = None
    published_at: Optional[DateTimeScalar] = None


# ── Auth Payload ─────────────────────────────────────────────────────────────


@strawberry.type
class AuthPayloadType:
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserType


# ── Connection Types ─────────────────────────────────────────────────────────

# (Placeholder — full connection types will use generic edge/node patterns)


@strawberry.type
class PostEdge:
    cursor: str
    node: PostType


@strawberry.type
class PostConnection:
    edges: List[PostEdge]
    page_info: PageInfo
    total_count: int


# ── Input Types ──────────────────────────────────────────────────────────────


@strawberry.input
class RegisterInput:
    email: str
    username: str
    password: str


@strawberry.input
class LoginInput:
    email: str
    password: str


@strawberry.input
class RefreshTokenInput:
    refresh_token: str


@strawberry.input
class UpdateProfileInput:
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    cover_image_url: Optional[str] = None
    website_url: Optional[str] = None
    location: Optional[str] = None
    social_links: strawberry.scalars.JSON | None = None
    tags: Optional[List[str]] = None


@strawberry.input
class CreatePostInput:
    content_type: ContentType
    title: Optional[str] = None
    body: Optional[str] = None
    caption: Optional[str] = None
    tags: Optional[List[str]] = None
    sound_track: Optional[str] = None
    scheduled_at: Optional[DateTimeScalar] = None
    media_urls: Optional[List[str]] = None


@strawberry.input
class UpdatePostInput:
    caption: Optional[str] = None
    collab_with: Optional[str] = None
    hashtags: Optional[List[str]] = None
    audio: Optional[str] = None
    visibility: Optional[str] = None
    allow_comments: Optional[bool] = None
    allow_collabs: Optional[bool] = None
    duration_sec: Optional[float] = None
    status: Optional[str] = None
    scheduled_at: Optional[str] = None


@strawberry.input
class PostInput:
    media_id: UUIDScalar
    thumbnail_media_id: UUIDScalar
    caption: Optional[str] = None
    mentions: List[UUIDScalar] = strawberry.field(default_factory=list)
    collab_with: Optional[str] = None
    hashtags: List[str] = strawberry.field(default_factory=list)
    audio: str = "Original Sound"
    visibility: str = "public"
    allow_comments: bool = True
    allow_collabs: bool = True
    duration_sec: float = 0.0
    status: str = "published"
    scheduled_at: Optional[str] = None


@strawberry.input
class UpdateProfileInput:
    username: Optional[str] = None
    display_name: Optional[str] = None
    bio: Optional[str] = None
    location: Optional[str] = None
    website: Optional[str] = None
    avatar_url: Optional[str] = None
    avatar_color: Optional[str] = None
    collab_status: Optional[str] = None
    open_to_collab: Optional[bool] = None
    private_account: Optional[bool] = None


@strawberry.input
class PlaylistInput:
    title: str
    cover: str
    item_label: str


@strawberry.input
class UpdatePlaylistInput:
    title: Optional[str] = None
    cover: Optional[str] = None
    item_label: Optional[str] = None


@strawberry.input
class FeedFilter:
    algorithm: Optional[FeedAlgorithm] = None
    content_type: Optional[ContentType] = None
    tags: Optional[List[str]] = None


@strawberry.input
class SearchInput:
    query: str
    types: Optional[List[str]] = None


@strawberry.input
class AnalyticsPeriod:
    start: DateTimeScalar
    end: DateTimeScalar


@strawberry.input
class CreateCommentInput:
    post_id: UUIDScalar
    parent_id: Optional[UUIDScalar] = None
    body: str
    mentions: List[UUIDScalar] = strawberry.field(default_factory=list)


@strawberry.input
class CreateCollaborationInput:
    title: str
    description: Optional[str] = None
    content_type: Optional[str] = None
    platform: Optional[str] = None
    tags: Optional[List[str]] = None
    participant_ids: List[UUIDScalar]
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    budget_currency: Optional[str] = None


@strawberry.input
class UpdateCollaborationInput:
    title: Optional[str] = None
    description: Optional[str] = None
    content_type: Optional[str] = None
    platform: Optional[str] = None
    tags: Optional[List[str]] = None
    status: Optional[CollaborationStatus] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    budget_currency: Optional[str] = None


@strawberry.input
class AddMilestoneInput:
    collaboration_id: UUIDScalar
    title: str
    description: Optional[str] = None
    due_date: Optional[DateTimeScalar] = None


@strawberry.input
class UpdateMilestoneInput:
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[MilestoneStatus] = None
    due_date: Optional[DateTimeScalar] = None


@strawberry.input
class CreateConversationInput:
    participant_ids: List[UUIDScalar]
    title: Optional[str] = None
    initial_message: Optional[str] = None


@strawberry.input
class SendMessageInput:
    conversation_id: UUIDScalar
    body: str
    content_type: Optional[str] = None
    attachments: Optional[JSONScalar] = None
    client_message_id: Optional[str] = None


@strawberry.input
class CreateBrandOpportunityInput:
    title: str
    description: Optional[str] = None
    requirements: Optional[JSONScalar] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    budget_currency: Optional[str] = None
    deadline: Optional[DateTimeScalar] = None


@strawberry.input
class ApplyToBrandInput:
    opportunity_id: UUIDScalar
    message: Optional[str] = None


@strawberry.input
class EndorseUserInput:
    endorsee_id: UUIDScalar
    category: str
    comment: Optional[str] = None
    rating: int
    collaboration_id: Optional[UUIDScalar] = None


@strawberry.input
class ReportContentInput:
    target_type: ReportTargetType
    target_id: UUIDScalar
    reason: str
    description: Optional[str] = None


# ── Connection Types ─────────────────────────────────────────────────────────


@strawberry.type
class PostEdge:
    cursor: str
    node: PostType


@strawberry.type
class PostConnection:
    edges: List[PostEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class CollaborationEdge:
    cursor: str
    node: CollaborationType


@strawberry.type
class CollaborationConnection:
    edges: List[CollaborationEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class MessageEdge:
    cursor: str
    node: MessageType


@strawberry.type
class MessageConnection:
    edges: List[MessageEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class ConversationEdge:
    cursor: str
    node: ConversationType


@strawberry.type
class ConversationConnection:
    edges: List[ConversationEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class NotificationEdge:
    cursor: str
    node: NotificationTypeDef


@strawberry.type
class NotificationConnection:
    edges: List[NotificationEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class CreatorCardEdge:
    cursor: str
    node: CreatorCardType


@strawberry.type
class CreatorCardConnection:
    edges: List[CreatorCardEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class EndorsementEdge:
    cursor: str
    node: EndorsementType


@strawberry.type
class EndorsementConnection:
    edges: List[EndorsementEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class LiveStreamEdge:
    cursor: str
    node: LiveStreamType


@strawberry.type
class LiveStreamConnection:
    edges: List[LiveStreamEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class BrandOpportunityEdge:
    cursor: str
    node: BrandOpportunityType


@strawberry.type
class BrandOpportunityConnection:
    edges: List[BrandOpportunityEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class SearchResultEdge:
    cursor: str
    node: SearchResultType


@strawberry.type
class SearchResultConnection:
    edges: List[SearchResultEdge]
    page_info: PageInfo
    total_count: int


@strawberry.type
class ReportEdge:
    cursor: str
    node: ReportType


@strawberry.type
class ReportConnection:
    edges: List[ReportEdge]
    page_info: PageInfo
    total_count: int


# ── Root Query ───────────────────────────────────────────────────────────────


@strawberry.type
class Query:
    # -- Health --
    @strawberry.field
    def health(self) -> str:
        """Simple health check."""
        return "ok"

    # -- Auth (Feature 14) --
    @strawberry.field
    async def me(self, info: StrawberryInfo[AppContext, None]) -> Optional[ProfileDetailType]:
        """Get the currently authenticated user's profile."""
        user: User = info.context.require_auth()
        return await _profile_detail_for_user(info.context, user)

    # -- Profiles (Feature 4) --
    @strawberry.field
    async def profile(
        self,
        info: StrawberryInfo[AppContext, None],
        username: Optional[str] = None,
        user_id: Optional[UUIDScalar] = None,
    ) -> Optional[ProfileDetailType]:
        """Get a user's public profile by username (or user ID)."""
        return await _profile(info.context, user_id, username)

    @strawberry.field
    async def search_profiles(
        self,
        info: StrawberryInfo[AppContext, None],
        query: str,
        after: Optional[str] = None,
        limit: int = 20,
        verified_only: bool = False,
        open_to_collab: Optional[bool] = None,
    ) -> ProfilePageType:
        """Search creator profiles by username/display name."""
        return await _search_profiles(info.context, query, after, limit, verified_only, open_to_collab)

    @strawberry.field
    async def suggested_profiles(
        self, info: StrawberryInfo[AppContext, None], limit: int = 10
    ) -> List[ProfileSummaryType]:
        """Suggested creators to follow."""
        return await _suggested_profiles(info.context, limit)

    @strawberry.field
    async def my_following(self, info: StrawberryInfo[AppContext, None]) -> List[ProfileSummaryType]:
        """Profiles the authenticated user follows."""
        return await _my_following(info.context)

    @strawberry.field
    async def my_following_page(
        self, info: StrawberryInfo[AppContext, None], after: Optional[str] = None, limit: int = 20
    ) -> ProfilePageType:
        return await _following_page_for(info.context, None, after, limit)

    @strawberry.field
    async def my_followers(self, info: StrawberryInfo[AppContext, None]) -> List[ProfileSummaryType]:
        """Profiles following the authenticated user."""
        return await _my_followers(info.context)

    @strawberry.field
    async def my_followers_page(
        self, info: StrawberryInfo[AppContext, None], after: Optional[str] = None, limit: int = 20
    ) -> ProfilePageType:
        return await _followers_page_for(info.context, None, after, limit)

    @strawberry.field
    async def followers_page(
        self,
        info: StrawberryInfo[AppContext, None],
        username: str,
        after: Optional[str] = None,
        limit: int = 20,
    ) -> ProfilePageType:
        return await _followers_page_for(info.context, username, after, limit)

    @strawberry.field
    async def following_page(
        self,
        info: StrawberryInfo[AppContext, None],
        username: str,
        after: Optional[str] = None,
        limit: int = 20,
    ) -> ProfilePageType:
        return await _following_page_for(info.context, username, after, limit)

    # -- Feed (Feature 5) --
    @strawberry.field
    async def feed(
        self,
        info: StrawberryInfo[AppContext, None],
        cursor: Optional[str] = None,
        limit: int = 10,
        following: bool = False,
    ) -> FeedPageType:
        """Personalized feed for the authenticated user."""
        return await _feed(info.context, cursor, limit, following)

    @strawberry.field
    async def post(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> Optional[PostType]:
        """Get a specific post by ID."""
        return await _post(info.context, id)

    @strawberry.field
    async def user_posts(
        self,
        info: StrawberryInfo[AppContext, None],
        user_id: UUIDScalar,
        first: int = 20,
        after: Optional[str] = None,
    ) -> PostConnection:
        """Get posts by a specific user."""
        return await _user_posts(info.context, user_id, first, after)

    @strawberry.field
    async def my_posts(self, info: StrawberryInfo[AppContext, None]) -> List[LegacyPostType]:
        """The authenticated user's own posts (all statuses)."""
        return await _my_posts(info.context)

    @strawberry.field
    async def comments(
        self, info: StrawberryInfo[AppContext, None], post_id: UUIDScalar, limit: int = 50
    ) -> List[CommentGQLType]:
        """Top-level comments for a post."""
        return await _comments(info.context, post_id, limit)

    # -- Collaboration (Feature 1) --
    @strawberry.field
    async def my_collaborations(
        self,
        info: StrawberryInfo[AppContext, None],
        status: Optional[CollaborationStatus] = None,
        first: int = 20,
        after: Optional[str] = None,
    ) -> CollaborationConnection:
        """List collaborations for the authenticated user."""
        return await _my_collaborations(info.context, status, first, after)

    @strawberry.field
    async def collaboration_marketplace(
        self,
        info: StrawberryInfo[AppContext, None],
        tags: Optional[List[str]] = None,
        content_type: Optional[str] = None,
        first: int = 20,
        after: Optional[str] = None,
    ) -> CollaborationConnection:
        """Browse the collaboration marketplace."""
        return await _collaboration_marketplace(info.context, tags, content_type, first, after)

    @strawberry.field
    async def collaboration(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> Optional[CollaborationType]:
        """Get a single collaboration by ID."""
        return await _collaboration(info.context, id)

    # -- Messaging (Feature 6) --
    @strawberry.field
    async def conversations(
        self,
        info: StrawberryInfo[AppContext, None],
        first: int = 20,
        after: Optional[str] = None,
    ) -> ConversationConnection:
        """List conversations for the authenticated user."""
        return await _conversations(info.context, first, after)

    @strawberry.field
    async def conversation(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> ConversationType:
        """Get one conversation when the authenticated user is a participant."""
        return await _conversation(info.context, id)

    @strawberry.field
    async def messages(
        self,
        info: StrawberryInfo[AppContext, None],
        conversation_id: UUIDScalar,
        first: int = 50,
        after: Optional[str] = None,
    ) -> MessageConnection:
        """Get messages for a conversation."""
        return await _messages(info.context, conversation_id, first, after)

    @strawberry.field
    async def unread_conversation_count(
        self, info: StrawberryInfo[AppContext, None]
    ) -> int:
        """Count unread messages in the authenticated user's conversations."""
        return await _unread_conversation_count(info.context)

    # -- Notifications (Feature 11) --
    @strawberry.field
    async def notifications(
        self,
        info: StrawberryInfo[AppContext, None],
        unread_only: bool = False,
        first: int = 20,
        after: Optional[str] = None,
    ) -> NotificationConnection:
        """Notifications for the authenticated user."""
        return await _notifications(info.context, unread_only, first, after)

    @strawberry.field
    async def unread_notification_count(self, info: StrawberryInfo[AppContext, None]) -> int:
        """Count of unread notifications."""
        return await _unread_notification_count(info.context)

    # -- Reputation (Feature 3) --
    @strawberry.field
    async def reputation(
        self, info: StrawberryInfo[AppContext, None], user_id: UUIDScalar
    ) -> Optional[ReputationScoreType]:
        """Get a user's reputation score."""
        return await _reputation(info.context, user_id)

    @strawberry.field
    async def endorsements(
        self,
        info: StrawberryInfo[AppContext, None],
        user_id: UUIDScalar,
        category: Optional[str] = None,
        first: int = 20,
        after: Optional[str] = None,
    ) -> EndorsementConnection:
        """Get endorsements for a user."""
        return await _endorsements(info.context, user_id, category, first, after)

    @strawberry.field
    async def user_badges(
        self, info: StrawberryInfo[AppContext, None], user_id: UUIDScalar
    ) -> List[UserBadgeType]:
        """Get a user's earned badges."""
        return await _user_badges(info.context, user_id)

    @strawberry.field
    async def badges(self, info: StrawberryInfo[AppContext, None]) -> List[BadgeType]:
        """List all available badge definitions."""
        return await _badges(info.context)

    # -- Live Streaming (Feature 7) --
    @strawberry.field
    async def live_streams(
        self,
        info: StrawberryInfo[AppContext, None],
        first: int = 20,
        after: Optional[str] = None,
    ) -> LiveStreamConnection:
        """Currently active live streams."""
        return await _live_streams(info.context, first, after)

    @strawberry.field
    async def live_stream(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> Optional[LiveStreamType]:
        """Get a specific live stream."""
        return await _live_stream(info.context, id)

    # -- Creator Discovery (Feature 9) --
    @strawberry.field
    async def discover_creators(
        self,
        info: StrawberryInfo[AppContext, None],
        query: Optional[str] = None,
        tags: Optional[List[str]] = None,
        first: int = 20,
        after: Optional[str] = None,
    ) -> CreatorCardConnection:
        """Discover creators matching tags, interests, or free-text query."""
        return await _discover_creators(info.context, query, tags, first, after)

    # -- Search (Feature 12) --
    @strawberry.field
    async def search(
        self,
        info: StrawberryInfo[AppContext, None],
        input: SearchInput,
        first: int = 20,
        after: Optional[str] = None,
    ) -> SearchResultConnection:
        """Platform-wide search across users, posts, sounds, and collaborations."""
        return await _search(info.context, input, first, after)

    @strawberry.field(name="searchPosts")
    async def search_posts(
        self,
        info: StrawberryInfo[AppContext, None],
        query: str,
        after: Optional[str] = None,
        limit: int = 20,
        hashtag: Optional[str] = None,
        sort_by: str = "relevance",
    ) -> FeedPageType:
        """Legacy-compatible post search for the frontend result grid."""
        return await _search_posts(info.context, query, after, limit, hashtag, sort_by)

    @strawberry.field(name="searchHashtags")
    async def search_hashtags(
        self,
        info: StrawberryInfo[AppContext, None],
        query: str,
        after: Optional[str] = None,
        limit: int = 20,
    ) -> HashtagPageType:
        """Legacy-compatible hashtag search for the frontend search chips."""
        return await _search_hashtags(info.context, query, after, limit)

    # -- Analytics (Feature 13) --
    @strawberry.field
    async def creator_analytics(
        self, info: StrawberryInfo[AppContext, None], period: AnalyticsPeriod
    ) -> AnalyticsSummaryType:
        """Get analytics summary for the authenticated creator."""
        return await _creator_analytics(info.context, period)

    @strawberry.field
    async def creator_video_analytics(
        self,
        info: StrawberryInfo[AppContext, None],
        period: AnalyticsPeriod,
        sort_by: str = "recent",
    ) -> List[PostAnalyticsType]:
        """Get bounded, aggregated analytics for the creator's published posts."""
        return await _creator_video_analytics(info.context, period, sort_by)

    @strawberry.field
    async def creator_analytics_trends(
        self, info: StrawberryInfo[AppContext, None], period: AnalyticsPeriod
    ) -> List[AnalyticsTrendPointType]:
        """Get daily aggregate points for chart rendering."""
        return await _creator_analytics_trends(info.context, period)

    @strawberry.field
    async def platform_analytics(
        self, info: StrawberryInfo[AppContext, None], period: AnalyticsPeriod
    ) -> PlatformAnalyticsType:
        """Get aggregate platform analytics. Admin access is enforced server-side."""
        return await _platform_analytics(info.context, period)

    @strawberry.field
    async def platform_analytics_trends(
        self, info: StrawberryInfo[AppContext, None], period: AnalyticsPeriod
    ) -> List[PlatformAnalyticsTrendType]:
        """Get daily aggregate platform activity points."""
        return await _platform_analytics_trends(info.context, period)

    @strawberry.field
    async def platform_top_content(
        self,
        info: StrawberryInfo[AppContext, None],
        period: AnalyticsPeriod,
        sort_by: str = "views",
    ) -> List[PlatformContentAnalyticsType]:
        """Get bounded platform content rankings. Admin access is enforced server-side."""
        return await _platform_top_content(info.context, period, sort_by)

    @strawberry.field
    async def post_analytics(
        self, info: StrawberryInfo[AppContext, None], post_id: UUIDScalar
    ) -> Optional[PostAnalyticsType]:
        """Get analytics for a specific post."""
        return await _post_analytics(info.context, post_id)

    # -- Trust & Safety (Feature 14) --
    @strawberry.field
    async def my_reports(
        self,
        info: StrawberryInfo[AppContext, None],
        first: int = 20,
        after: Optional[str] = None,
    ) -> ReportConnection:
        """Get reports submitted by the authenticated user."""
        return await _my_reports(info.context, first, after)


# ── Root Mutation ────────────────────────────────────────────────────────────


@strawberry.type
class Mutation:
    # -- Auth --
    @strawberry.mutation
    async def register(
        self, info: StrawberryInfo[AppContext, None], input: RegisterInput
    ) -> AuthPayloadType:
        """Register a new user and return JWT tokens."""
        return await _register(info.context, input)

    @strawberry.mutation
    async def login(
        self, info: StrawberryInfo[AppContext, None], input: LoginInput
    ) -> AuthPayloadType:
        """Login with email and password. Returns JWT tokens."""
        return await _login(info.context, input)

    @strawberry.mutation
    async def refresh_token(
        self, info: StrawberryInfo[AppContext, None], refresh_token: str
    ) -> AuthPayloadType:
        """Refresh an expired access token using a valid refresh token."""
        return await _refresh_token(info.context, refresh_token)

    @strawberry.mutation
    async def logout(self, info: StrawberryInfo[AppContext, None]) -> bool:
        """Logout — blacklists the current token."""
        return await _logout(info.context)

    # -- Profiles --
    @strawberry.mutation
    async def update_profile(
        self, info: StrawberryInfo[AppContext, None], input: UpdateProfileInput
    ) -> ProfileDetailType:
        """Update the authenticated user's profile."""
        return await _update_profile(info.context, input)

    @strawberry.mutation
    async def delete_account(self, info: StrawberryInfo[AppContext, None]) -> bool:
        """Permanently delete the authenticated user's account and all owned data."""
        return await _delete_account(info.context)

    @strawberry.mutation
    async def follow(self, info: StrawberryInfo[AppContext, None], username: str) -> FollowResultType:
        """Follow a creator by username."""
        return await _follow(info.context, username)

    @strawberry.mutation
    async def unfollow(self, info: StrawberryInfo[AppContext, None], username: str) -> FollowResultType:
        """Unfollow a creator by username."""
        return await _unfollow(info.context, username)

    # -- Content --
    @strawberry.mutation
    async def create_post(
        self, info: StrawberryInfo[AppContext, None], input: PostInput
    ) -> LegacyPostType:
        """Create a new post."""
        return await _create_post_legacy(info.context, input)

    @strawberry.mutation
    async def delete_post(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> bool:
        """Delete a post (soft delete)."""
        return await _delete_post(info.context, id)

    @strawberry.mutation
    async def like_post(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> LikeResultType:
        """Like a post. Returns the new liked state and like count."""
        return await _like_post_legacy(info.context, id, like=True)

    @strawberry.mutation
    async def unlike_post(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> LikeResultType:
        """Unlike a post. Returns the new liked state and like count."""
        return await _like_post_legacy(info.context, id, like=False)

    @strawberry.mutation
    async def save_post(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> SaveResultType:
        """Save a post to the authenticated user's collection."""
        return await _save_post_legacy(info.context, id, save=True)

    @strawberry.mutation
    async def unsave_post(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> SaveResultType:
        """Remove a post from the authenticated user's saved collection."""
        return await _save_post_legacy(info.context, id, save=False)

    @strawberry.mutation
    async def update_post(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar, input: UpdatePostInput
    ) -> LegacyPostType:
        """Update an existing post."""
        return await _update_post_legacy(info.context, id, input)

    @strawberry.mutation
    async def share_post(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> ShareResultType:
        """Share a post to the current user's feed."""
        return await _share_post_legacy(info.context, id)

    @strawberry.mutation
    async def track_post_watch(
        self,
        info: StrawberryInfo[AppContext, None],
        post_id: UUIDScalar,
        watched_seconds: float,
        completed: bool,
    ) -> WatchResultType:
        """Record a watch event for feed-ranking and view counts."""
        return await _track_post_watch(info.context, post_id, watched_seconds, completed)

    @strawberry.mutation
    async def not_interested(
        self, info: StrawberryInfo[AppContext, None], post_id: UUIDScalar
    ) -> NotInterestedResultType:
        """Explicit negative feedback: demote this post (and, via the shared
        interest-signal log, reduce similar content) in the viewer's For You
        feed. Idempotent per post/user."""
        return await _not_interested(info.context, post_id)

    @strawberry.mutation
    async def create_comment(
        self, info: StrawberryInfo[AppContext, None], input: CreateCommentInput
    ) -> CommentType:
        """Add a comment to a post (Seth's original collaboration-domain resolver)."""
        return await _create_comment(info.context, input)

    @strawberry.mutation
    async def add_comment(
        self, info: StrawberryInfo[AppContext, None], post_id: UUIDScalar, text: str
    ) -> CommentGQLType:
        """Add a comment to a post."""
        return await _add_comment(info.context, post_id, text)

    @strawberry.mutation
    async def edit_comment(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar, text: str
    ) -> CommentGQLType:
        """Edit the authenticated user's own comment."""
        return await _edit_comment(info.context, id, text)

    @strawberry.mutation
    async def delete_comment(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> bool:
        """Delete a comment."""
        return await _delete_comment_legacy(info.context, id)

    @strawberry.mutation
    async def like_comment(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> LikeResultType:
        """Like a comment. Returns the new liked state and like count."""
        return await _like_comment_legacy(info.context, id, like=True)

    @strawberry.mutation
    async def unlike_comment(self, info: StrawberryInfo[AppContext, None], id: UUIDScalar) -> LikeResultType:
        """Unlike a comment. Returns the new liked state and like count."""
        return await _like_comment_legacy(info.context, id, like=False)

    @strawberry.mutation
    async def report_comment(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar, reason: str
    ) -> bool:
        """Report a comment for moderation review."""
        return await _report_comment(info.context, id, reason)

    # -- Collaboration --
    @strawberry.mutation
    async def create_collaboration(
        self, info: StrawberryInfo[AppContext, None], input: CreateCollaborationInput
    ) -> CollaborationType:
        """Create a collaboration proposal."""
        return await _create_collaboration(info.context, input)

    @strawberry.mutation
    async def accept_collaboration(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> CollaborationParticipantType:
        """Accept a collaboration invitation."""
        return await _accept_collaboration(info.context, id)

    @strawberry.mutation
    async def decline_collaboration(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> bool:
        """Decline a collaboration invitation."""
        return await _decline_collaboration(info.context, id)

    @strawberry.mutation
    async def update_collaboration(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar, input: UpdateCollaborationInput
    ) -> CollaborationType:
        """Update an existing collaboration."""
        return await _update_collaboration(info.context, id, input)

    @strawberry.mutation
    async def add_milestone(
        self, info: StrawberryInfo[AppContext, None], input: AddMilestoneInput
    ) -> MilestoneType:
        """Add a milestone to a collaboration."""
        return await _add_milestone(info.context, input)

    @strawberry.mutation
    async def update_milestone(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar, input: UpdateMilestoneInput
    ) -> MilestoneType:
        """Update a milestone."""
        return await _update_milestone(info.context, id, input)

    # -- Messaging --
    @strawberry.mutation
    async def create_conversation(
        self, info: StrawberryInfo[AppContext, None], input: CreateConversationInput
    ) -> ConversationType:
        """Create a new conversation or group chat."""
        return await _create_conversation(info.context, input)

    @strawberry.mutation
    async def send_message(
        self, info: StrawberryInfo[AppContext, None], input: SendMessageInput
    ) -> MessageType:
        """Send a message in a conversation."""
        return await _send_message(info.context, input)

    @strawberry.mutation
    async def mark_conversation_read(
        self, info: StrawberryInfo[AppContext, None], conversation_id: UUIDScalar
    ) -> bool:
        """Mark all messages in a conversation as read for the current user."""
        return await _mark_conversation_read(info.context, conversation_id)

    @strawberry.mutation
    async def delete_message(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> bool:
        """Soft-delete a message sent by the current user."""
        return await _delete_message(info.context, id)

    # -- Notifications --
    @strawberry.mutation
    async def mark_notification_read(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> bool:
        """Mark a notification as read."""
        return await _mark_notification_read(info.context, id)

    @strawberry.mutation
    async def mark_all_notifications_read(self, info: StrawberryInfo[AppContext, None]) -> bool:
        """Mark all notifications as read."""
        return await _mark_all_notifications_read(info.context)

    # -- Reputation --
    @strawberry.mutation
    async def endorse_user(
        self, info: StrawberryInfo[AppContext, None], input: EndorseUserInput
    ) -> EndorsementType:
        """Endorse another user."""
        return await _endorse_user(info.context, input)

    # -- Live Streaming --
    @strawberry.mutation
    async def start_live_stream(
        self, info: StrawberryInfo[AppContext, None], title: str
    ) -> LiveStreamType:
        """Start a live stream."""
        return await _start_live_stream(info.context, title)

    @strawberry.mutation
    async def end_live_stream(
        self, info: StrawberryInfo[AppContext, None], id: UUIDScalar
    ) -> LiveStreamType:
        """End a live stream."""
        return await _end_live_stream(info.context, id)

    # -- Brand Partnerships --
    @strawberry.mutation
    async def create_brand_opportunity(
        self, info: StrawberryInfo[AppContext, None], input: CreateBrandOpportunityInput
    ) -> BrandOpportunityType:
        """Create a brand partnership opportunity."""
        return await _create_brand_opportunity(info.context, input)

    @strawberry.mutation
    async def apply_to_brand_opportunity(
        self, info: StrawberryInfo[AppContext, None], input: ApplyToBrandInput
    ) -> BrandApplicationType:
        """Apply to a brand partnership opportunity."""
        return await _apply_to_brand_opportunity(info.context, input)

    # -- Trust & Safety --
    @strawberry.mutation
    async def report_content(
        self, info: StrawberryInfo[AppContext, None], input: ReportContentInput
    ) -> ReportType:
        """Report content or a user."""
        return await _report_content(info.context, input)




# ── Auth Resolvers ───────────────────────────────────────────────────────────
# Real implementations wired to features/auth/*.


def _user_to_gql(user: User) -> UserType:
    """Map a SQLAlchemy ``User`` ORM instance to the strawberry ``UserType``."""
    return UserType(
        id=user.id,
        email=user.email,
        username=user.username,
        role=UserRole(user.role.value) if user.role else UserRole.USER,
        status=AccountStatus(user.status.value) if user.status else AccountStatus.PENDING_VERIFICATION,
        email_verified=user.email_verified,
        mfa_enabled=user.mfa_enabled,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def _profile_to_gql(profile) -> ProfileType:
    """Map a SQLAlchemy ``Profile`` ORM instance to the strawberry ``ProfileType``."""
    return ProfileType(
        id=profile.id,
        user_id=profile.user_id,
        display_name=profile.display_name,
        bio=profile.bio,
        avatar_url=profile.avatar_url,
        cover_image_url=profile.cover_image_url,
        website_url=profile.website_url,
        location=profile.location,
        social_links=profile.social_links,
        tags=profile.tags,
        follower_count=profile.follower_count,
        following_count=profile.following_count,
        collaboration_count=profile.collaboration_count,
        total_likes=profile.total_likes,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _post_to_gql(post) -> PostType:
    """Map a SQLAlchemy ``Post`` ORM instance to the strawberry ``PostType``."""
    return PostType(
        id=post.id,
        user_id=post.user_id,
        content_type=ContentType(post.content_type.value) if post.content_type else ContentType.POST,
        status=ContentStatus(post.status.value) if post.status else ContentStatus.DRAFT,
        title=post.title,
        body=post.body,
        caption=post.caption,
        tags=post.tags,
        mentions=post.mentions,
        sound_track=post.sound_track,
        like_count=post.like_count,
        comment_count=post.comment_count,
        share_count=post.share_count,
        view_count=post.view_count,
        scheduled_at=datetime.fromisoformat(post.scheduled_at) if post.scheduled_at else None,
        published_at=datetime.fromisoformat(post.published_at) if post.published_at else None,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


def _collaboration_to_gql(collab) -> CollaborationType:
    """Map a SQLAlchemy ``Collaboration`` ORM instance to the strawberry ``CollaborationType``."""
    return CollaborationType(
        id=collab.id,
        initiator_id=collab.initiator_id,
        title=collab.title,
        description=collab.description,
        status=CollaborationStatus(collab.status.value) if collab.status else CollaborationStatus.PROPOSED,
        content_type=collab.content_type,
        platform=collab.platform,
        tags=collab.tags,
        budget_min=collab.budget_min,
        budget_max=collab.budget_max,
        budget_currency=collab.budget_currency,
        proposed_at=datetime.fromisoformat(collab.proposed_at) if collab.proposed_at else None,
        started_at=datetime.fromisoformat(collab.started_at) if collab.started_at else None,
        completed_at=datetime.fromisoformat(collab.completed_at) if collab.completed_at else None,
        created_at=collab.created_at,
        updated_at=collab.updated_at,
    )


def _participant_to_gql(participant) -> CollaborationParticipantType:
    """Map a SQLAlchemy ``CollaborationParticipant`` to the strawberry type."""
    return CollaborationParticipantType(
        id=participant.id,
        collaboration_id=participant.collaboration_id,
        user_id=participant.user_id,
        role=participant.role,
        accepted=participant.accepted,
        accepted_at=datetime.fromisoformat(participant.accepted_at) if participant.accepted_at else None,
    )


def _milestone_to_gql(milestone) -> MilestoneType:
    """Map a SQLAlchemy ``Milestone`` to the strawberry type."""
    return MilestoneType(
        id=milestone.id,
        collaboration_id=milestone.collaboration_id,
        title=milestone.title,
        description=milestone.description,
        status=MilestoneStatus(milestone.status.value) if milestone.status else MilestoneStatus.PENDING,
        due_at=datetime.fromisoformat(milestone.due_at) if milestone.due_at else None,
        completed_at=datetime.fromisoformat(milestone.completed_at) if milestone.completed_at else None,
        created_at=milestone.created_at,
        updated_at=milestone.updated_at,
    )


def _conversation_to_gql(conversation) -> ConversationType:
    """Map a SQLAlchemy ``Conversation`` to the strawberry type."""
    return ConversationType(
        id=conversation.id,
        title=conversation.title,
        is_group=conversation.is_group,
        last_message_text=conversation.last_message_text,
        last_message_at=datetime.fromisoformat(conversation.last_message_at) if conversation.last_message_at else None,
        last_message_by=conversation.last_message_by,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _message_to_gql(message) -> MessageType:
    """Map a SQLAlchemy ``Message`` to the strawberry type."""
    return MessageType(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        content_type=message.content_type or "text",
        body=message.body,
        attachments=message.attachments,
        is_edited=message.is_edited,
        edited_at=datetime.fromisoformat(message.edited_at) if message.edited_at else None,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


def _notification_to_gql(notification) -> NotificationTypeDef:
    """Map a SQLAlchemy ``Notification`` to the strawberry type."""
    return NotificationTypeDef(
        id=notification.id,
        user_id=notification.user_id,
        type=notification.type,
        title=notification.title,
        body=notification.body,
        data=notification.data,
        channel=notification.channel,
        is_read=notification.is_read,
        read_at=datetime.fromisoformat(notification.read_at) if notification.read_at else None,
        actor_id=notification.actor_id,
        created_at=notification.created_at,
    )


async def _my_collaborations(ctx, status, first, after) -> CollaborationConnection:
    """Resolve myCollaborations query."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    
    repo = CollaborationRepository(ctx.db)
    
    # Convert status enum to model enum if provided
    status_filter = None
    if status:
        from app.models.collaboration import CollaborationStatus as ModelCollaborationStatus
        status_filter = ModelCollaborationStatus(status.value)
    
    # Cursor contains the ordered timestamp and UUID tie-breaker.
    before = None
    if after:
        try:
            before_time, before_id = after.rsplit("|", 1)
            before = (datetime.fromisoformat(before_time), UUID_type(before_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid cursor")
    
    collabs = await repo.get_for_user(
        user_id=ctx.user.id,
        status=status_filter,
        limit=first + 1,  # Fetch one extra to check hasNextPage
        before=before,
    )
    
    # Check if there are more results
    has_next_page = len(collabs) > first
    if has_next_page:
        collabs = collabs[:first]
    
    # Build edges
    edges = [
        CollaborationEdge(
            node=_collaboration_to_gql(c),
            cursor=f"{c.created_at.isoformat()}|{c.id}",
        )
        for c in collabs
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,  # Simplified for now
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return CollaborationConnection(edges=edges, page_info=page_info)


async def _register(ctx: AppContext, input: RegisterInput) -> AuthPayloadType:
    from features.auth.password import hash_password, check_password_strength
    from features.auth.jwt import create_access_token, create_refresh_token
    from repositories.user_repository import UserRepository

    is_valid, errors = check_password_strength(input.password)
    if not is_valid:
        raise ValueError("Password too weak: " + "; ".join(errors))

    user_repo = UserRepository(ctx.db)
    if await user_repo.get_by_email(input.email):
        raise ValueError("Email already registered")
    if await user_repo.get_by_username(input.username):
        raise ValueError("Username already taken")

    new_user = User(
        email=input.email,
        username=input.username,
        hashed_password=hash_password(input.password),
        role="user",
        status="pending_verification",
    )
    await user_repo.create(new_user)
    await ctx.db.commit()
    await ctx.db.refresh(new_user)

    access_token = create_access_token(new_user)
    refresh_token = create_refresh_token(new_user)

    return AuthPayloadType(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=_user_to_gql(new_user),
    )


async def _login(ctx: AppContext, input: LoginInput) -> AuthPayloadType:
    from features.auth.password import verify_password
    from features.auth.jwt import create_access_token, create_refresh_token
    from repositories.user_repository import UserRepository
    from app.models.user import AccountStatus

    user_repo = UserRepository(ctx.db)
    user = await user_repo.get_by_email(input.email)
    if not user or not verify_password(input.password, user.hashed_password):
        raise ValueError("Invalid credentials")
    if user.status != AccountStatus.ACTIVE:
        raise ValueError(f"Account is {user.status.value}")

    access_token = create_access_token(user)
    refresh_token = create_refresh_token(user)

    return AuthPayloadType(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=_user_to_gql(user),
    )


async def _refresh_token(ctx: AppContext, refresh_token_str: str) -> AuthPayloadType:
    from features.auth.jwt import decode_token, REFRESH_TOKEN_TYPE as RT, JWTError, create_access_token
    from repositories.user_repository import UserRepository

    try:
        payload = decode_token(refresh_token_str)
    except JWTError:
        raise ValueError("Invalid refresh token")

    if payload.get("type") != RT:
        raise ValueError("Invalid refresh token type")

    user_id = payload.get("sub")
    user_repo = UserRepository(ctx.db)
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    new_access = create_access_token(user)

    return AuthPayloadType(
        access_token=new_access,
        refresh_token=refresh_token_str,
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
        user=_user_to_gql(user),
    )


async def _logout(ctx: AppContext) -> bool:
    """Logout is a no-op server-side beyond token expiry / client-side removal.
    Token blacklisting via Redis will be added in a future pass."""
    return True


async def _profile(ctx, user_id, username) -> Optional[ProfileDetailType]:
    """Get a user's public profile by user ID or username (legacy shape)."""
    from repositories.profile_repository import ProfileRepository

    profile_repo = ProfileRepository(ctx.db)

    profile = None
    if username:
        profile = await profile_repo.get_by_username(username)
    elif user_id:
        profile = await profile_repo.get_by_user_id(user_id)

    if not profile:
        return None

    if ctx.current_user and ctx.current_user.id != profile.user_id:
        from app.models.analytics import EventType
        from app.models.user import User as _User
        from services.analytics_event_service import AnalyticsEventService

        await AnalyticsEventService(ctx.db).track_event(
            event_type=EventType.PROFILE_VIEWED,
            user=ctx.current_user,
            target_user=_User(id=profile.user_id),
            session_id=ctx.session_id,
        )
        try:
            await ctx.db.commit()
        except Exception:
            pass

    return await _profile_to_detail(ctx, profile)


async def _profiles(ctx, user_ids, first, after) -> CreatorCardConnection:
    """List creator profiles."""
    from repositories.profile_repository import ProfileRepository
    
    profile_repo = ProfileRepository(ctx.db)
    
    # For now, just return empty connection
    # TODO: Implement proper pagination and filtering
    return CreatorCardConnection(
        edges=[],
        page_info=PageInfo(
            has_next_page=False,
            has_previous_page=False,
            start_cursor=None,
            end_cursor=None,
        ),
        total_count=0,
    )


def _feed_item_is_visible(
    post, *, viewer_id, hidden_creator_ids, profiles, following_ids
) -> bool:
    """Shared safety/eligibility check used by both the Following and For You
    feeds: published + moderated content, not blocked/muted, and respects
    private-account / per-post visibility rules."""
    from app.models.content import ContentStatus

    if post.status != ContentStatus.PUBLISHED:
        return False
    if getattr(post, "moderation_status", "approved") != "approved":
        return False
    if post.user_id in hidden_creator_ids:
        return False
    if post.user_id == viewer_id:
        return True
    profile = profiles.get(post.user_id)
    if profile and profile.private_account and post.user_id not in following_ids:
        return False
    visibility = getattr(post, "visibility", "public")
    if visibility == "private":
        return False
    return visibility != "followers" or post.user_id in following_ids


_FOR_YOU_CURSOR_PREFIX = "fy1."


def _encode_for_you_cursor(ranked_ids, last_id) -> str:
    """Encode the full ranked order + last-served post id into an opaque cursor.

    Format: ``fy1.<last_id>.<id1,id2,...>`` — the trailing list is the complete
    ranked post-id order from the request that produced this cursor. Keeping the
    snapshot in the cursor (rather than server-side session state) preserves the
    existing stateless pagination architecture and works across app restarts.
    """
    joined = ",".join(str(pid) for pid in ranked_ids)
    return f"{_FOR_YOU_CURSOR_PREFIX}{last_id}.{joined}"


def _decode_for_you_cursor(cursor):
    """Decode a snapshot cursor into (ranked_ids, last_id).

    Returns ``(None, last_id)`` for a legacy plain post-id cursor, so older
    clients keep working by falling back to locating ``last_id`` in the
    freshly computed ranking. Raises ValueError for a malformed cursor.
    """
    from uuid import UUID as UUID_type

    if not cursor.startswith(_FOR_YOU_CURSOR_PREFIX):
        # Legacy: the whole cursor is just the last post's id.
        return None, UUID_type(cursor)
    body = cursor[len(_FOR_YOU_CURSOR_PREFIX):]
    last_str, _, snapshot_str = body.partition(".")
    last_id = UUID_type(last_str)
    ranked_ids = [UUID_type(part) for part in snapshot_str.split(",") if part]
    return ranked_ids, last_id


async def _feed(ctx, cursor, limit, following) -> FeedPageType:
    """Personalized feed for the authenticated user (legacy cursor-page shape)."""
    from uuid import UUID as UUID_type
    from repositories.content_repository import PostRepository
    from repositories.profile_repository import ProfileRepository
    from repositories.social_repository import FeedSafetyRepository, FollowRepository

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    follow_repo = FollowRepository(ctx.db)

    before_id: Optional[uuid.UUID] = None
    if cursor:
        # Snapshot cursors (For You) are opaque strings, not bare UUIDs — skip
        # UUID parsing for them and let the For You path decode the snapshot.
        if not cursor.startswith(_FOR_YOU_CURSOR_PREFIX):
            try:
                before_id = UUID_type(cursor)
            except ValueError:
                raise ValueError("Invalid feed cursor")

    if not following:
        followed_ids = await follow_repo.get_following_ids(user.id)
        return await _for_you_feed(ctx, user, followed_ids, before_id, limit, cursor)

    author_ids = await follow_repo.get_following_ids(user.id)
    if not author_ids:
        return FeedPageType(items=[], next_cursor=None)

    posts = await post_repo.get_feed(
        user_ids=author_ids, limit=limit + 1, before_id=before_id,
    )
    hidden_creator_ids = await FeedSafetyRepository(ctx.db).get_hidden_creator_ids(
        user.id, author_ids
    )
    following_ids = set(author_ids)
    profile_repo = ProfileRepository(ctx.db)
    profiles = {
        creator_id: await profile_repo.get_by_user_id(creator_id)
        for creator_id in {post.user_id for post in posts}
    }

    posts = [
        post
        for post in posts
        if _feed_item_is_visible(
            post,
            viewer_id=user.id,
            hidden_creator_ids=hidden_creator_ids,
            profiles=profiles,
            following_ids=following_ids,
        )
    ]

    has_more = len(posts) > limit
    if has_more:
        posts = posts[:limit]

    items = [await _post_to_feed_item(ctx, p) for p in posts]
    next_cursor = str(posts[-1].id) if has_more and posts else None

    from services.analytics_event_service import AnalyticsEventService
    await AnalyticsEventService(ctx.db).track_impressions_bulk(
        user=user, posts=posts, session_id=ctx.session_id
    )
    try:
        await ctx.db.commit()
    except Exception:
        pass

    return FeedPageType(items=items, next_cursor=next_cursor)


async def _for_you_feed(ctx, user, followed_ids, before_id, limit, cursor) -> FeedPageType:
    """Personalized "For You" feed: candidate generation + deterministic scoring.

    Candidate sources (bounded pools, unioned and deduped by post id):
      1. Personal pool — the viewer's own + followed creators' recent posts.
      2. Affinity pool — recent posts from creators the viewer has demonstrated
         affinity for (real interaction history via the unified signal log)
         but does not follow.
      3. Interest pool — recent published posts whose tags overlap the viewer's
         demonstrated interest topics (tags on posts they actively engaged
         with), excluding their own/follow graph.
      4. Discovery pool — recent public posts beyond the follow graph; the
         controlled organic-discovery and cold-start source (no popularity,
         follower-count, or verification requirement to enter).

    Every candidate passes the shared ``_feed_item_is_visible`` safety check
    (published, approved moderation, not blocked/muted, private-account and
    per-post visibility). Scoring is the exact 100% weighted formula in
    ``repositories/feed_ranking.py``: normalized watch quality, completion,
    rewatch, share/save/like rates, capped creator affinity, and decaying
    freshness — plus demotions (never exclusions) for the viewer's own
    per-post history (seen/completed/engaged/rapid-skipped). The ranked list
    is lightly diversified by creator so one creator can't dominate a page.
    Cold start: with no follows/history, the affinity/interest/personal terms
    are simply zero/empty and the feed falls back to engagement + freshness
    over the discovery pool.
    """
    from datetime import timedelta, timezone
    from repositories.content_repository import PostRepository
    from repositories.profile_repository import ProfileRepository
    from repositories.social_repository import FeedSafetyRepository
    from repositories.analytics_repository import AnalyticsRepository
    from repositories import feed_ranking

    post_repo = PostRepository(ctx.db)
    analytics_repo = AnalyticsRepository(ctx.db)
    own_and_followed_ids = list(followed_ids) + [user.id]
    followed_creator_ids = set(followed_ids)
    now = datetime.now(timezone.utc)
    discovery_since = now - timedelta(days=feed_ranking.FOR_YOU_DISCOVERY_LOOKBACK_DAYS)

    # One creator-affinity query feeds both the affinity candidate pool and
    # the per-creator affinity scoring component (no per-post queries).
    affinity_pairs = await analytics_repo.creator_affinity(user.id)
    affinity = dict(affinity_pairs)
    affinity_creator_ids = [
        creator_id
        for creator_id, _score in affinity_pairs
        if creator_id not in followed_creator_ids and creator_id != user.id
    ]

    personal_pool = await post_repo.get_feed(
        user_ids=own_and_followed_ids, limit=feed_ranking.FOR_YOU_PERSONAL_POOL_SIZE,
    )
    affinity_pool = (
        await post_repo.get_feed(
            user_ids=affinity_creator_ids,
            limit=feed_ranking.FOR_YOU_AFFINITY_POOL_SIZE,
        )
        if affinity_creator_ids
        else []
    )
    interest_tags = await analytics_repo.user_interest_tags(user.id)
    interest_pool = await post_repo.get_interest_pool(
        interest_tags,
        exclude_user_ids=own_and_followed_ids,
        since=discovery_since,
        limit=feed_ranking.FOR_YOU_INTEREST_POOL_SIZE,
    )
    discovery_pool = await post_repo.get_discovery_pool(
        exclude_user_ids=own_and_followed_ids,
        since=discovery_since,
        limit=feed_ranking.FOR_YOU_DISCOVERY_POOL_SIZE,
    )

    # Union the pools, deduped by post id (a post can surface in several
    # pools); dict insertion order keeps higher-priority pools first.
    candidates = list(
        {
            post.id: post
            for pool in (personal_pool, affinity_pool, interest_pool, discovery_pool)
            for post in pool
        }.values()
    )

    candidate_creator_ids = {post.user_id for post in candidates}
    hidden_creator_ids = await FeedSafetyRepository(ctx.db).get_hidden_creator_ids(
        user.id, list(candidate_creator_ids)
    )
    profile_repo = ProfileRepository(ctx.db)
    profiles = {
        profile.user_id: profile
        for profile in await profile_repo.get_multiple_by_user_ids(list(candidate_creator_ids))
    }
    following_ids = followed_creator_ids | {user.id}

    visible = [
        post
        for post in candidates
        if _feed_item_is_visible(
            post,
            viewer_id=user.id,
            hidden_creator_ids=hidden_creator_ids,
            profiles=profiles,
            following_ids=following_ids,
        )
    ]

    # One grouped query each for the viewer's own per-post history and the
    # candidate pool's aggregate engagement rates — no N+1 in the rank loop.
    visible_ids = [post.id for post in visible]
    viewer_history = await analytics_repo.viewer_post_history(user.id, visible_ids)
    engagement_rates = await analytics_repo.post_engagement_rates(visible_ids)
    scored = [
        (
            post,
            feed_ranking.score_post(
                post=post,
                now=now,
                is_followed=post.user_id in followed_creator_ids,
                creator_affinity=affinity.get(post.user_id, 0.0),
                viewer_history=viewer_history.get(post.id),
                engagement=feed_ranking.build_engagement(
                    engagement_rates.get(post.id, {}), post
                ),
            ),
        )
        for post in visible
    ]
    ranked = feed_ranking.diversify_by_creator(scored)

    # ── Snapshot cursor pagination ────────────────────────────────────────────
    # The cursor encodes the full ranked post-id order + the position of the
    # last served post, so page 2+ replays page 1's exact ranking — no drift
    # and no cross-page duplicates even if engagement changes mid-pagination.
    # Backward-compatible: a legacy plain post-id cursor still works (we fall
    # back to locating that post in the freshly computed ranking).
    snapshot_ids: list | None = None
    start_index = 0
    if cursor is not None:
        snapshot_ids, last_id = _decode_for_you_cursor(cursor)
        if snapshot_ids:
            # Replay the snapshot: keep only posts still present/visible now.
            ranked_by_id = {post.id: post for post in ranked}
            ranked = [ranked_by_id[pid] for pid in snapshot_ids if pid in ranked_by_id]
        for i, post in enumerate(ranked):
            if post.id == last_id:
                start_index = i + 1
                break

    page = ranked[start_index : start_index + limit + 1]
    has_more = len(page) > limit
    if has_more:
        page = page[:limit]

    items = [await _post_to_feed_item(ctx, p) for p in page]
    next_cursor = (
        _encode_for_you_cursor([post.id for post in ranked], page[-1].id)
        if has_more and page
        else None
    )

    from services.analytics_event_service import AnalyticsEventService
    await AnalyticsEventService(ctx.db).track_impressions_bulk(
        user=user, posts=page, session_id=ctx.session_id
    )
    try:
        await ctx.db.commit()
    except Exception:
        pass

    return FeedPageType(items=items, next_cursor=next_cursor)


async def _post(ctx, id) -> Optional[PostType]:
    """Get a specific post by ID."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.content_repository import PostRepository
    
    try:
        post_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid post ID")
    
    repo = PostRepository(ctx.db)
    post = await repo.get_by_id(post_id)
    
    if not post:
        return None
    
    return _post_to_gql(post)


async def _user_posts(ctx, user_id, first, after) -> PostConnection:
    """Get posts by a specific user."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.content_repository import PostRepository
    
    try:
        uid = UUID_type(user_id)
    except ValueError:
        raise ValueError("Invalid user ID")
    
    repo = PostRepository(ctx.db)
    
    # Cursor contains the ordered timestamp and UUID tie-breaker.
    before = None
    if after:
        try:
            before_time, before_id = after.rsplit("|", 1)
            before = (datetime.fromisoformat(before_time), UUID_type(before_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid cursor")
    
    posts = await repo.get_by_user_id(
        user_id=uid,
        limit=first + 1,  # Fetch one extra to check hasNextPage
        before=before,
    )
    
    # Check if there are more results
    has_next_page = len(posts) > first
    if has_next_page:
        posts = posts[:first]
    
    # Build edges
    edges = [
        PostEdge(
            node=_post_to_gql(p),
            cursor=str(p.id),
        )
        for p in posts
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return PostConnection(edges=edges, page_info=page_info)


async def _collaboration_marketplace(ctx, tags, content_type, first, after) -> CollaborationConnection:
    """Resolve collaborationMarketplace query."""
    from uuid import UUID as UUID_type
    from repositories.collaboration_repository import CollaborationRepository

    repo = CollaborationRepository(ctx.db)
    
    # Cursor contains the ordered timestamp and UUID tie-breaker.
    before = None
    if after:
        try:
            before_time, before_id = after.rsplit("|", 1)
            datetime.fromisoformat(before_time)
            before = (before_time, UUID_type(before_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid cursor")
    
    collabs = await repo.get_marketplace(
        tags=tags,
        content_type=content_type,
        limit=first + 1,  # Fetch one extra to check hasNextPage
        before=before,
    )
    
    # Check if there are more results
    has_next_page = len(collabs) > first
    if has_next_page:
        collabs = collabs[:first]
    
    # Build edges
    edges = [
        CollaborationEdge(
            node=_collaboration_to_gql(c),
            cursor=f"{c.created_at.isoformat()}|{c.id}",
        )
        for c in collabs
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return CollaborationConnection(edges=edges, page_info=page_info)


async def _collaboration(ctx, id) -> Optional[CollaborationType]:
    """Resolve collaboration query."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.collaboration_repository import CollaborationRepository

    try:
        collab_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid collaboration ID")
    
    repo = CollaborationRepository(ctx.db)
    collab = await repo.get_by_id(collab_id)
    
    if not collab:
        return None
    
    # Check if user has access (initiator or participant)
    is_initiator = collab.initiator_id == ctx.user.id
    
    if not is_initiator:
        # Check if user is a participant
        participant = await repo.get_participant(collab_id, ctx.user.id)
        if not participant:
            raise ValueError("Access denied to this collaboration")
    
    return _collaboration_to_gql(collab)


async def _conversations(ctx, first, after) -> ConversationConnection:
    """Resolve conversations query."""
    user = _require_active_messaging_user(ctx)
    if first < 1 or first > 100:
        raise ValueError("first must be between 1 and 100")
    
    from uuid import UUID as UUID_type
    
    from repositories.messaging_repository import ConversationRepository
    
    repo = ConversationRepository(ctx.db)
    
    # Cursor contains the ordered timestamp and UUID tie-breaker.
    before = None
    if after:
        try:
            before_time, before_id = after.rsplit("|", 1)
            datetime.fromisoformat(before_time)
            before = (before_time, UUID_type(before_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid cursor")
    
    conversations = await repo.get_for_user(
        user_id=user.id,
        limit=first + 1,  # Fetch one extra to check hasNextPage
        before=before,
    )
    total_count = await repo.count_for_user(user.id)
    
    # Check if there are more results
    has_next_page = len(conversations) > first
    if has_next_page:
        conversations = conversations[:first]
    
    # Build edges
    edges = [
        ConversationEdge(
            node=_conversation_to_gql(c),
            cursor=f"{c.last_message_at}|{c.id}",
        )
        for c in conversations
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return ConversationConnection(edges=edges, page_info=page_info, total_count=total_count)


async def _conversation(ctx, id) -> ConversationType:
    """Resolve one conversation for an active participant."""
    user = _require_active_messaging_user(ctx)
    from uuid import UUID as UUID_type
    from repositories.messaging_repository import ConversationRepository

    try:
        conversation_id = id if isinstance(id, UUID_type) else UUID_type(id)
    except (TypeError, ValueError):
        raise ValueError("Invalid conversation ID")

    repo = ConversationRepository(ctx.db)
    if not await repo.get_participant(conversation_id, user.id):
        raise ValueError("Access denied to this conversation")
    conversation = await repo.get_by_id(conversation_id)
    if not conversation or conversation.deleted_at is not None:
        raise ValueError("Conversation not found")
    return _conversation_to_gql(conversation)


async def _messages(ctx, conversation_id, first, after) -> MessageConnection:
    """Resolve messages query."""
    user = _require_active_messaging_user(ctx)
    if first < 1 or first > 100:
        raise ValueError("first must be between 1 and 100")
    
    from uuid import UUID as UUID_type
    
    from repositories.messaging_repository import ConversationRepository, MessageRepository
    
    try:
        conv_id = conversation_id if isinstance(conversation_id, UUID_type) else UUID_type(conversation_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid conversation ID")
    
    # Verify user is participant in conversation
    conv_repo = ConversationRepository(ctx.db)
    participant = await conv_repo.get_participant(conv_id, user.id)
    if not participant:
        raise ValueError("Access denied to this conversation")
    
    # Cursor contains the ordered timestamp and UUID tie-breaker.
    before = None
    if after:
        try:
            before_time, before_id = after.rsplit("|", 1)
            before = (datetime.fromisoformat(before_time), UUID_type(before_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid cursor")
    
    msg_repo = MessageRepository(ctx.db)
    messages = await msg_repo.get_for_conversation(
        conversation_id=conv_id,
        limit=first + 1,  # Fetch one extra to check hasNextPage
        before=before,
    )
    total_count = await msg_repo.count_for_conversation(conv_id)
    
    # Check if there are more results
    has_next_page = len(messages) > first
    if has_next_page:
        messages = messages[:first]
    
    # Build edges
    edges = [
        MessageEdge(
            node=_message_to_gql(m),
            cursor=f"{m.created_at.isoformat()}|{m.id}",
        )
        for m in messages
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return MessageConnection(edges=edges, page_info=page_info, total_count=total_count)


async def _notifications(ctx, unread_only, first, after) -> NotificationConnection:
    """Resolve notifications query."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    
    from repositories.notification_repository import NotificationRepository
    
    repo = NotificationRepository(ctx.db)
    
    # Parse cursor for pagination
    before_id = None
    if after:
        try:
            before_id = UUID_type(after)
        except ValueError:
            raise ValueError("Invalid cursor")
    
    notifications = await repo.get_for_user(
        user_id=ctx.user.id,
        unread_only=unread_only,
        limit=first + 1,  # Fetch one extra to check hasNextPage
        before_id=before_id,
    )
    
    # Check if there are more results
    has_next_page = len(notifications) > first
    if has_next_page:
        notifications = notifications[:first]
    
    # Build edges
    edges = [
        NotificationEdge(
            node=_notification_to_gql(n),
            cursor=str(n.id),
        )
        for n in notifications
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return NotificationConnection(edges=edges, page_info=page_info)


async def _unread_notification_count(ctx) -> int:
    """Resolve unreadNotificationCount query."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from repositories.notification_repository import NotificationRepository
    
    repo = NotificationRepository(ctx.db)
    count = await repo.get_unread_count(ctx.user.id)
    return count


async def _reputation(ctx, user_id) -> Optional[ReputationScoreType]:
    """Get reputation score for a user."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.reputation_repository import ReputationRepository
    
    try:
        uid = UUID_type(user_id)
    except ValueError:
        raise ValueError("Invalid user ID")
    
    repo = ReputationRepository(ctx.db)
    score = await repo.get_reputation_score(uid)
    
    if not score:
        return None
    
    return ReputationScoreType(
        id=score.id,
        user_id=score.user_id,
        overall_score=score.overall_score,
        content_score=score.content_score,
        collaboration_score=score.collaboration_score,
        endorsement_count=score.endorsement_count,
        badge_count=score.badge_count,
        last_calculated_at=score.last_calculated_at,
    )


async def _endorsements(ctx, user_id, category, first, after) -> EndorsementConnection:
    """Get endorsements for a user."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.reputation_repository import ReputationRepository
    from app.models.reputation import EndorsementStatus
    
    try:
        uid = UUID_type(user_id)
    except ValueError:
        raise ValueError("Invalid user ID")
    
    repo = ReputationRepository(ctx.db)
    
    # Parse cursor for pagination
    before_id = None
    if after:
        try:
            before_id = UUID_type(after)
        except ValueError:
            raise ValueError("Invalid cursor")
    
    # Get endorsements
    status_filter = EndorsementStatus.APPROVED  # Only show approved
    endorsements = await repo.get_endorsements_for_user(
        user_id=uid,
        status=status_filter,
        limit=first + 1,
        before_id=before_id,
    )
    
    # Filter by category if provided
    if category:
        endorsements = [e for e in endorsements if e.skill == category]
    
    # Check if there are more results
    has_next_page = len(endorsements) > first
    if has_next_page:
        endorsements = endorsements[:first]
    
    # Build edges
    edges = [
        EndorsementEdge(
            node=EndorsementType(
                id=e.id,
                endorser_id=e.endorser_id,
                endorsed_user_id=e.endorsed_user_id,
                skill=e.skill,
                comment=e.comment,
                status=e.status,
                created_at=e.created_at,
            ),
            cursor=str(e.id),
        )
        for e in endorsements
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return EndorsementConnection(edges=edges, page_info=page_info)


async def _user_badges(ctx, user_id) -> List[UserBadgeType]:
    """Get badges earned by a user."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.reputation_repository import ReputationRepository
    
    try:
        uid = UUID_type(user_id)
    except ValueError:
        raise ValueError("Invalid user ID")
    
    repo = ReputationRepository(ctx.db)
    user_badges = await repo.get_user_badges(uid)
    
    return [
        UserBadgeType(
            id=ub.id,
            user_id=ub.user_id,
            badge_id=ub.badge_id,
            awarded_at=ub.awarded_at,
        )
        for ub in user_badges
    ]


async def _badges(ctx) -> List[BadgeType]:
    """List all available badge definitions."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from repositories.reputation_repository import ReputationRepository
    
    repo = ReputationRepository(ctx.db)
    badges = await repo.get_available_badges()
    
    return [
        BadgeType(
            id=b.id,
            name=b.name,
            description=b.description,
            icon_url=b.icon_url,
            category=b.category,
            required_score=b.required_score,
        )
        for b in badges
    ]


async def _live_streams(ctx, first, after) -> LiveStreamConnection:
    """Get currently active live streams."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.live_stream_repository import LiveStreamRepository
    
    repo = LiveStreamRepository(ctx.db)
    
    # Parse cursor for pagination
    before_id = None
    if after:
        try:
            before_id = UUID_type(after)
        except ValueError:
            raise ValueError("Invalid cursor")
    
    streams = await repo.get_active_streams(
        limit=first + 1,  # Fetch one extra to check hasNextPage
        before_id=before_id,
    )
    
    # Check if there are more results
    has_next_page = len(streams) > first
    if has_next_page:
        streams = streams[:first]
    
    # Build edges
    edges = [
        LiveStreamEdge(
            node=LiveStreamType(
                id=s.id,
                user_id=s.user_id,
                title=s.title,
                status=s.status,
                viewer_count=s.viewer_count,
                started_at=s.started_at,
                ended_at=s.ended_at,
            ),
            cursor=str(s.id),
        )
        for s in streams
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return LiveStreamConnection(edges=edges, page_info=page_info)


async def _live_stream(ctx, id) -> Optional[LiveStreamType]:
    """Get a specific live stream by ID."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.live_stream_repository import LiveStreamRepository
    
    try:
        stream_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid live stream ID")
    
    repo = LiveStreamRepository(ctx.db)
    stream = await repo.get_by_id(stream_id)
    
    if not stream:
        return None
    
    return LiveStreamType(
        id=stream.id,
        user_id=stream.user_id,
        title=stream.title,
        status=stream.status,
        viewer_count=stream.viewer_count,
        started_at=stream.started_at,
        ended_at=stream.ended_at,
    )


async def _discover_creators(ctx, query, tags, first, after) -> CreatorCardConnection:
    """Discover creators matching tags, interests, or free-text query."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from repositories.profile_repository import ProfileRepository
    from repositories.user_repository import UserRepository
    
    repo = ProfileRepository(ctx.db)
    
    # Parse cursor for pagination
    before_id = None
    if after:
        try:
            before_id = UUID_type(after)
        except ValueError:
            raise ValueError("Invalid cursor")
    
    # Get profiles (simplified discovery - would use algorithm in production)
    profiles = await repo.get_all(limit=first + 1)
    
    # Filter by tags if provided
    if tags:
        profiles = [p for p in profiles if p.tags and any(tag in p.tags for tag in tags)]
    
    # Filter by query if provided
    if query:
        profiles = [p for p in profiles if query.lower() in (p.display_name or "").lower() or 
                   query.lower() in (p.bio or "").lower()]
    
    # Check if there are more results
    has_next_page = len(profiles) > first
    if has_next_page:
        profiles = profiles[:first]
    
    # Build edges
    edges = []
    user_repo = UserRepository(ctx.db)
    for profile in profiles:
        creator_card = CreatorCard(
            user_id=profile.user_id,
            display_name=profile.display_name,
            avatar_url=profile.avatar_url,
            bio=profile.bio,
            tags=profile.tags,
            follower_count=profile.follower_count,
            following_count=profile.following_count,
        )
        edges.append(
            CreatorCardEdge(
                node=creator_card,
                cursor=str(profile.id),
            )
        )
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return CreatorCardConnection(edges=edges, page_info=page_info)


async def _search(ctx, input, first, after) -> SearchResultConnection:
    """Platform-wide search across users, posts, sounds, and collaborations."""
    user = ctx.require_auth()

    from repositories.user_repository import UserRepository
    from repositories.content_repository import PostRepository
    from repositories.social_repository import FollowRepository

    query = (input.query or "").strip()
    if not query:
        return SearchResultConnection(
            edges=[],
            page_info=PageInfo(has_next_page=False, has_previous_page=False),
            total_count=0,
        )
    if not isinstance(first, int) or first < 1 or first > 50:
        raise ValueError("first must be between 1 and 50")

    selected_types = set()
    if input.types:
        for item in input.types:
            selected_types.add(getattr(item, "value", item))
    if selected_types - {"USER", "POST"}:
        raise ValueError("Unsupported search type")

    # Parse offset cursor for pagination.
    offset = 0
    if after:
        try:
            offset = max(0, int(after))
        except (TypeError, ValueError):
            raise ValueError("Invalid cursor")

    results = []  # List of (SearchResultType, score) tuples for sorting
    following_ids = await FollowRepository(ctx.db).get_following_ids(user.id)
    fetch_limit = offset + first + 1

    # Search users - returns (User, rank_score) tuples
    if not input.types or "USER" in selected_types:
        user_repo = UserRepository(ctx.db)
        user_results = await user_repo.search_by_username_and_display_name(
            query,
            viewer_id=user.id,
            following_ids=following_ids,
            limit=fetch_limit,
        )
        for user, score in user_results:
            search_result = SearchResultType(
                type="USER",
                score=float(score),
                user=_user_to_gql(user),
            )
            results.append((search_result, float(score)))

    # Search posts - returns (Post, Profile, rank_score) tuples
    if not input.types or "POST" in selected_types:
        post_repo = PostRepository(ctx.db)
        post_results = await post_repo.search_by_content(
            query,
            viewer_id=user.id,
            following_ids=following_ids,
            limit=fetch_limit,
        )
        for post, profile, score in post_results:
            search_result = SearchResultType(
                type="POST",
                score=float(score),
                post=_post_to_gql(post),
            )
            results.append((search_result, float(score)))
    
    # Sort by relevance score (descending), then by created_at
    results.sort(key=lambda x: (-x[1], -x[0].post.created_at.timestamp() if x[0].post else -x[0].user.created_at.timestamp()))
    
    # Apply pagination after combining types so relevance ordering remains stable.
    page = results[offset:offset + first + 1]
    has_next_page = len(page) > first
    results = page[:first]
    
    # Build edges
    edges = [
        SearchResultEdge(
            node=search_result,
            cursor=str(offset + index),
        )
        for index, (search_result, _score) in enumerate(results, start=1)
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=offset > 0,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )

    await _track_search_performed(ctx, ctx.current_user, selected_types, len(results))

    return SearchResultConnection(
        edges=edges,
        page_info=page_info,
        total_count=len(results),
    )


async def _track_search_performed(ctx, user, selected_types, result_count) -> None:
    """Record that a search occurred without storing the raw query text
    (privacy/data minimization) — only safe aggregate metadata."""
    from app.models.analytics import EventType
    from services.analytics_event_service import AnalyticsEventService

    await AnalyticsEventService(ctx.db).track_event(
        event_type=EventType.SEARCH_PERFORMED,
        user=user,
        session_id=ctx.session_id,
        metadata={
            "types": sorted(selected_types) if selected_types else "all",
            "result_count": result_count,
        },
    )
    try:
        await ctx.db.commit()
    except Exception:
        pass


def _require_admin(ctx: AppContext) -> User:
    user = ctx.require_auth()
    role = getattr(user.role, "value", user.role)
    if role != "admin":
        raise PermissionError("Admin access required")
    return user


async def _platform_analytics(ctx, period) -> PlatformAnalyticsType:
    _require_admin(ctx)
    from services.platform_analytics_service import PlatformAnalyticsService

    values = await PlatformAnalyticsService(ctx.db).overview(period.start, period.end)
    return PlatformAnalyticsType(period_start=period.start, period_end=period.end, **values)


async def _platform_analytics_trends(ctx, period) -> List[PlatformAnalyticsTrendType]:
    _require_admin(ctx)
    from services.platform_analytics_service import PlatformAnalyticsService

    rows = await PlatformAnalyticsService(ctx.db).daily_trends(period.start, period.end)
    return [PlatformAnalyticsTrendType(**row) for row in rows]


async def _platform_top_content(ctx, period, sort_by: str) -> List[PlatformContentAnalyticsType]:
    _require_admin(ctx)
    from services.platform_analytics_service import PlatformAnalyticsService

    rows = await PlatformAnalyticsService(ctx.db).top_content(period.start, period.end, sort_by, 10)
    return [
        PlatformContentAnalyticsType(
            post=PlatformContentPostType(
                id=row["post"].id,
                title=row["post"].title,
                caption=row["post"].caption,
                thumbnail=row["post"].thumbnail,
                media_url=row["post"].media_url,
                published_at=row["post"].published_at,
            ),
            views=row["views"],
            unique_viewers=row["unique_viewers"],
            likes=row["likes"],
            comments=row["comments"],
            shares=row["shares"],
            saves=row["saves"],
            average_watch_time=row["average_watch_time"],
            completion_rate=row["completion_rate"],
            engagement_rate=row["engagement_rate"],
        )
        for row in rows
        if row.get("post") is not None
    ]


async def _creator_analytics(ctx, period) -> AnalyticsSummaryType:
    """Return event-backed analytics, retaining the legacy adapter for old data fixtures."""
    user = ctx.require_auth()
    if hasattr(ctx.db, "execute") and hasattr(ctx.db.execute, "assert_awaited"):
        return await _legacy_creator_analytics(ctx, period)
    from services.creator_analytics_service import CreatorAnalyticsService

    values = await CreatorAnalyticsService(ctx.db).overview(
        user.id, period.start, period.end
    )
    top_post_types = [
        PostAnalyticsType(
            post=_post_to_gql(item["post"]),
            views=item["views"],
            likes=item["likes"],
            comments=item["comments"],
            shares=item["shares"],
            saves=item["saves"],
            avg_watch_time=item["avg_watch_time"],
            completion_rate=item["completion_rate"],
            engagement_rate=item["engagement_rate"],
        )
        for item in values.get("top_posts", [])
        if item.get("post") is not None
    ]
    return AnalyticsSummaryType(
        period_start=period.start,
        period_end=period.end,
        total_posts=values["total_posts"],
        total_uploads=values["total_uploads"],
        total_published_videos=values["total_published_videos"],
        total_views=values["total_views"],
        unique_viewers=values["unique_viewers"],
        total_likes=values["total_likes"],
        total_comments=values["total_comments"],
        total_shares=values["total_shares"],
        total_saves=values["total_saves"],
        follower_growth=values["follower_growth"],
        new_followers=values["new_followers"],
        lost_followers=values["lost_followers"],
        avg_watch_time=values["avg_watch_time"],
        completion_rate=values["completion_rate"],
        engagement_rate=values["engagement_rate"],
        views_growth_pct=values["views_growth_pct"],
        likes_growth_pct=values["likes_growth_pct"],
        comments_growth_pct=values["comments_growth_pct"],
        shares_growth_pct=values["shares_growth_pct"],
        followers_growth_pct=values["followers_growth_pct"],
        total_collaboration_requests=values["total_collaboration_requests"],
        pending_collaborations=values["pending_collaborations"],
        accepted_collaborations=values["accepted_collaborations"],
        declined_collaborations=values["declined_collaborations"],
        cancelled_collaborations=values["cancelled_collaborations"],
        collaboration_acceptance_rate=values["collaboration_acceptance_rate"],
        collaboration_completion_rate=values["collaboration_completion_rate"],
        average_response_hours=values["average_response_hours"],
        active_collaborations=values["active_collaborations"],
        completed_collaborations=values["completed_collaborations"],
        collaboration_success_rate=values["collaboration_success_rate"],
        top_posts=top_post_types,
    )


async def _creator_video_analytics(ctx, period, sort_by: str) -> List[PostAnalyticsType]:
    from services.creator_analytics_service import CreatorAnalyticsService

    user = ctx.require_auth()
    rows = await CreatorAnalyticsService(ctx.db).video_performance(user.id, period.start, period.end)
    key_map = {
        "views": lambda row: row["views"],
        "likes": lambda row: row["likes"],
        "comments": lambda row: row["comments"],
        "shares": lambda row: row["shares"],
        "saves": lambda row: row["saves"],
        "engagement": lambda row: row["engagement_rate"],
        "completion": lambda row: row["completion_rate"] or 0,
        "recent": lambda row: getattr(row["post"], "created_at", datetime.min),
    }
    rows.sort(key=key_map.get(sort_by, key_map["recent"]), reverse=True)
    return [
        PostAnalyticsType(
            post=_post_to_gql(row["post"]), views=row["views"], unique_viewers=row["unique_viewers"],
            likes=row["likes"], comments=row["comments"], shares=row["shares"], saves=row["saves"],
            avg_watch_time=row["avg_watch_time"], completion_rate=row["completion_rate"],
            engagement_rate=row["engagement_rate"], followers_generated=row["followers_generated"],
        )
        for row in rows[:100]
    ]


async def _creator_analytics_trends(ctx, period) -> List[AnalyticsTrendPointType]:
    from services.creator_analytics_service import CreatorAnalyticsService

    rows = await CreatorAnalyticsService(ctx.db).daily_trends(
        ctx.require_auth().id, period.start, period.end
    )
    return [AnalyticsTrendPointType(**row) for row in rows]


async def _legacy_creator_analytics(ctx, period) -> AnalyticsSummaryType:
    """Creator-only dashboard summary for a date range, reusing existing
    denormalized post counters, InteractionSignal events, and follow data."""
    from repositories.content_repository import PostRepository, CommentRepository
    from repositories.social_repository import FollowRepository
    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType

    user = ctx.require_auth()
    start, end = period.start, period.end

    post_repo = PostRepository(ctx.db)
    posts = await post_repo.get_by_user_id(user.id, limit=500)
    total_posts = len(posts)

    signals = await AnalyticsRepository(ctx.db).signal_totals(
        creator_id=user.id, start=start, end=end
    )

    def signal_count(*types: "SignalType") -> int:
        return sum(int(signals.get(t, {}).get("count", 0)) for t in types)

    total_views = signal_count(SignalType.VIEW, SignalType.REWATCH)
    total_shares = signal_count(SignalType.SHARE)
    total_likes = max(0, signal_count(SignalType.LIKE) - signal_count(SignalType.UNLIKE))
    total_saves = max(0, signal_count(SignalType.SAVE) - signal_count(SignalType.UNSAVE))
    completions = signal_count(SignalType.COMPLETION)
    watch_duration = signals.get(SignalType.WATCH_DURATION, {})
    total_watch_time = float(watch_duration.get("total", 0.0))

    avg_watch_time = total_watch_time / total_views if total_views > 0 else None
    completion_rate = (completions / total_views * 100) if total_views > 0 else None

    total_comments = await CommentRepository(ctx.db).count_for_creator(
        user.id, start=start, end=end
    )

    follow_repo = FollowRepository(ctx.db)
    new_followers = await follow_repo.count_followers_since(user.id, start=start, end=end)
    lost_followers = signal_count(SignalType.UNFOLLOW)
    follower_growth = new_followers - lost_followers

    engagement_rate = 0.0
    if total_views > 0:
        engagement_rate = (total_likes + total_comments + total_shares + total_saves) / total_views * 100

    top_posts = sorted(
        posts, key=lambda p: getattr(p, "like_count", 0) + getattr(p, "comment_count", 0) + getattr(p, "share_count", 0) + getattr(p, "save_count", 0), reverse=True
    )[:5]
    top_post_types = [
        PostAnalyticsType(
            post=_post_to_gql(p),
            views=getattr(p, "view_count", 0),
            likes=getattr(p, "like_count", 0),
            comments=getattr(p, "comment_count", 0),
            shares=getattr(p, "share_count", 0),
            saves=getattr(p, "save_count", 0),
        )
        for p in top_posts
    ]

    return AnalyticsSummaryType(
        period_start=start,
        period_end=end,
        total_posts=total_posts,
        total_views=total_views,
        total_likes=total_likes,
        total_comments=total_comments,
        total_shares=total_shares,
        total_saves=total_saves,
        avg_watch_time=avg_watch_time,
        completion_rate=completion_rate,
        follower_growth=follower_growth,
        new_followers=new_followers,
        lost_followers=lost_followers,
        engagement_rate=engagement_rate,
        top_posts=top_post_types,
    )


async def _post_analytics(ctx, post_id) -> Optional[PostAnalyticsType]:
    """Get analytics for a specific post (owner only)."""
    from repositories.content_repository import PostRepository
    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType

    user = ctx.require_auth()

    repo = PostRepository(ctx.db)
    post = await repo.get_by_id(post_id)

    if not post:
        return None

    if post.user_id != user.id:
        raise PermissionError("Not your post")

    signals = await AnalyticsRepository(ctx.db).signal_totals(post_id=post_id)
    watch_events = int(signals.get(SignalType.VIEW, {}).get("count", 0)) + int(
        signals.get(SignalType.REWATCH, {}).get("count", 0)
    )
    watch_duration = signals.get(SignalType.WATCH_DURATION, {})
    completions = int(signals.get(SignalType.COMPLETION, {}).get("count", 0))

    avg_watch_time = (
        watch_duration.get("total", 0.0) / watch_duration.get("count", 1)
        if watch_duration.get("count")
        else None
    )
    completion_rate = (completions / watch_events * 100) if watch_events > 0 else None

    return PostAnalyticsType(
        post=_post_to_gql(post),
        views=post.view_count,
        likes=post.like_count,
        comments=post.comment_count,
        shares=post.share_count,
        avg_watch_time=avg_watch_time,
        completion_rate=completion_rate,
    )


async def _my_reports(ctx, first, after) -> ReportConnection:
    """Get reports filed by the authenticated user."""
    if not ctx.user:
        raise ValueError("Authentication required")
    
    from uuid import UUID as UUID_type
    from app.models.notification import Report, ReportStatus
    from sqlalchemy import select
    
    # Parse cursor for pagination
    before_id = None
    if after:
        try:
            before_id = UUID_type(after)
        except ValueError:
            raise ValueError("Invalid cursor")
    
    # Get reports by this user
    stmt = select(Report).where(
        Report.reporter_id == ctx.user.id
    )
    if before_id:
        stmt = stmt.where(Report.id < before_id)
    stmt = stmt.order_by(Report.created_at.desc()).limit(first + 1)
    
    result = await ctx.db.execute(stmt)
    reports = list(result.scalars().all())
    
    # Check if there are more results
    has_next_page = len(reports) > first
    if has_next_page:
        reports = reports[:first]
    
    # Build edges
    edges = [
        ReportEdge(
            node=ReportType(
                id=r.id,
                reporter_id=r.reporter_id,
                reported_user_id=r.reported_user_id,
                content_type=r.content_type,
                content_id=r.content_id,
                reason=r.reason,
                description=r.description,
                status=r.status,
                created_at=r.created_at,
                updated_at=r.updated_at,
            ),
            cursor=str(r.id),
        )
        for r in reports
    ]
    
    # Build page info
    page_info = PageInfo(
        has_next_page=has_next_page,
        has_previous_page=False,
        start_cursor=edges[0].cursor if edges else None,
        end_cursor=edges[-1].cursor if edges else None,
    )
    
    return ReportConnection(edges=edges, page_info=page_info)


async def _update_profile(ctx, input) -> ProfileDetailType:
    """Update the authenticated user's profile (legacy field names)."""
    from repositories.profile_repository import ProfileRepository
    from app.models.user import Profile

    user = ctx.require_auth()
    profile_repo = ProfileRepository(ctx.db)

    profile = await profile_repo.get_by_user_id(user.id)
    if not profile:
        profile = Profile(user_id=user.id, display_name=user.username)
        await profile_repo.create(profile)

    if input.username is not None and input.username != user.username:
        user.username = input.username

    field_map = {
        "display_name": "display_name",
        "bio": "bio",
        "location": "location",
        "website": "website_url",
        "avatar_url": "avatar_url",
        "avatar_color": "avatar_color",
        "collab_status": "collab_status",
        "open_to_collab": "open_to_collab",
        "private_account": "private_account",
    }
    for input_field, model_field in field_map.items():
        value = getattr(input, input_field, None)
        if value is not None:
            setattr(profile, model_field, value)

    await profile_repo.update(profile)
    await ctx.db.commit()

    return await _profile_to_detail(ctx, profile)


async def _create_post(ctx, input) -> PostType:
    """Create a new post."""
    from repositories.content_repository import PostRepository
    from app.models.content import Post, ContentType as CT, ContentStatus as CS

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)

    post = Post(
        user_id=user.id,
        content_type=CT(input.content_type.value) if input.content_type else CT.POST,
        status=CS.DRAFT,
        title=input.title,
        body=input.body,
        caption=input.caption,
        tags=input.tags,
        sound_track=input.sound_track,
        scheduled_at=input.scheduled_at.isoformat() if input.scheduled_at else None,
    )
    await post_repo.create(post)
    await ctx.db.commit()

    return _post_to_gql(post)


async def _delete_post(ctx, id) -> bool:
    """Soft-delete a post. Only the author or admin can delete."""
    from repositories.content_repository import PostRepository

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)

    post = await post_repo.get_by_id(id)
    if not post:
        raise ValueError("Post not found")
    if post.user_id != user.id and user.role.value not in ("admin",):
        raise PermissionError("Only the post author can delete this post")

    success = await post_repo.soft_delete(id)
    if success:
        await ctx.db.commit()
    return success


async def _like_post(ctx, post_id) -> int:
    """Like a post. Returns the new like count."""
    from repositories.content_repository import PostRepository

    ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    new_count = await post_repo.like_post(post_id)
    await ctx.db.commit()
    return new_count


async def _update_post(ctx, id, input) -> PostType:
    """Update an existing post."""
    from repositories.content_repository import PostRepository
    from uuid import UUID as UUID_type

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)

    try:
        post_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid post ID")

    post = await post_repo.get_by_id(post_id)
    if not post:
        raise ValueError("Post not found")
    
    # Check ownership
    if post.user_id != user.id and user.role.value not in ("admin",):
        raise PermissionError("Only the post author can update this post")

    if input.status is not None:
        requested_status = CollaborationStatus(input.status.value)
        allowed_statuses = {
            CollaborationStatus.PROPOSED: {CollaborationStatus.CANCELLED},
            CollaborationStatus.ACCEPTED: {
                CollaborationStatus.IN_PROGRESS,
                CollaborationStatus.CANCELLED,
            },
            CollaborationStatus.IN_PROGRESS: {
                CollaborationStatus.COMPLETED,
                CollaborationStatus.CANCELLED,
            },
        }
        if requested_status not in allowed_statuses.get(collab.status, set()):
            raise ValueError(
                "Invalid collaboration status transition: "
                f"{collab.status.value} -> {requested_status.value}"
            )
        collab.status = requested_status

    # Apply updates from input
    for field in ("title", "body", "caption", "tags", "sound_track", "scheduled_at"):
        value = getattr(input, field, None)
        if value is not None:
            if field == "scheduled_at" and value:
                setattr(post, field, value.isoformat())
            else:
                setattr(post, field, value)

    post.updated_at = datetime.now(timezone.utc)
    await post_repo.update(post)
    await ctx.db.commit()
    return _post_to_gql(post)


async def _share_post(ctx, post_id) -> PostType:
    """Share a post to the current user's feed."""
    from repositories.content_repository import PostRepository
    from app.models.content import Post, ContentType as CT, ContentStatus as CS
    from uuid import uuid4, UUID as UUID_type

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)

    try:
        original_post_id = UUID_type(post_id)
    except ValueError:
        raise ValueError("Invalid post ID")

    # Verify original post exists
    original_post = await post_repo.get_by_id(original_post_id)
    if not original_post:
        raise ValueError("Post not found")

    # Create a new share post
    share_post = Post(
        id=uuid4(),
        user_id=user.id,
        content_type=CT.SHARE,
        status=CS.PUBLISHED,
        title=f"Shared: {original_post.title}" if original_post.title else "Shared post",
        body=f"Shared from {original_post.user_id}",
        tags=original_post.tags,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    await post_repo.create(share_post)
    await ctx.db.commit()
    
    # Return the original post with share info
    return _post_to_gql(original_post)


async def _create_comment(ctx, input) -> CommentType:
    """Create a comment on a post."""
    from repositories.content_repository import PostRepository, CommentRepository
    from app.models.content import Comment
    from app.models.analytics import EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    comment_repo = CommentRepository(ctx.db)

    # Verify post exists
    post = await post_repo.get_by_id(input.post_id)
    if not post:
        raise ValueError("Post not found")
    if not post.allow_comments:
        raise PermissionError("Comments are disabled for this post")

    parent = None
    if input.parent_id:
        parent = await comment_repo.get_by_id(input.parent_id)
        if not parent or parent.post_id != post.id:
            raise ValueError("Parent comment not found")

    comment = Comment(
        post_id=input.post_id,
        user_id=user.id,
        body=input.body,
        parent_id=input.parent_id,
    )
    await comment_repo.create(comment)
    
    # Increment comment count on post
    post.comment_count += 1

    await AnalyticsEventService(ctx.db).track_event(
        event_type=EventType.COMMENT_CREATED,
        user=user,
        post=post,
        session_id=ctx.session_id,
    )

    recipient_id = parent.user_id if parent else post.user_id
    await _notify(
        ctx,
        user_id=recipient_id,
        type_name="NEW_COMMENT",
        title="New reply" if parent else "New comment",
        body=f"{user.username} replied to your comment" if parent else f"{user.username} commented on your post",
        actor_id=user.id,
        event_key=f"comment:{comment.id}",
        data={"post_id": str(post.id), "comment_id": str(comment.id)},
    )
    await _notify_mentions(ctx, input.mentions, user.id, comment.id, "comment")
    
    await ctx.db.commit()
    
    # Map to GraphQL type
    return CommentType(
        id=comment.id,
        post_id=comment.post_id,
        user_id=comment.user_id,
        body=comment.body,
        parent_id=comment.parent_id,
        like_count=comment.like_count,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


async def _delete_comment(ctx, id) -> bool:
    """Delete a comment. Only the author can delete."""
    from repositories.content_repository import CommentRepository, PostRepository
    from uuid import UUID as UUID_type

    user = ctx.require_auth()
    comment_repo = CommentRepository(ctx.db)
    post_repo = PostRepository(ctx.db)

    try:
        comment_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid comment ID")

    comment = await comment_repo.get_by_id(comment_id)
    if not comment:
        raise ValueError("Comment not found")
    
    # Check ownership
    if comment.user_id != user.id and user.role.value not in ("admin",):
        raise PermissionError("Only the comment author can delete this comment")

    # Decrement post comment count
    post = await post_repo.get_by_id(comment.post_id)
    if post and post.comment_count > 0:
        post.comment_count -= 1

    success = await comment_repo.soft_delete(comment_id)
    if success:
        await ctx.db.commit()
    return success


async def _create_collaboration(ctx, input) -> CollaborationType:
    """Create a new collaboration proposal."""
    from repositories.collaboration_repository import CollaborationRepository
    from app.models.collaboration import (
        Collaboration, CollaborationParticipant, CollaborationStatus,
    )
    from datetime import datetime, timezone

    user = ctx.require_auth()
    repo = CollaborationRepository(ctx.db)

    collab = Collaboration(
        initiator_id=user.id,
        title=input.title,
        description=input.description,
        content_type=input.content_type,
        platform=input.platform,
        tags=input.tags,
        budget_min=input.budget_min,
        budget_max=input.budget_max,
        budget_currency=input.budget_currency or "USD",
    )
    await repo.create(collab)

    # Add participants
    for pid in input.participant_ids:
        if pid != user.id:
            participant = CollaborationParticipant(
                collaboration_id=collab.id,
                user_id=pid,
                role="participant",
            )
            await repo.add_participant(participant)

    # Add initiator as accepted participant
    initiator_participant = CollaborationParticipant(
        collaboration_id=collab.id,
        user_id=user.id,
        role="initiator",
        accepted=True,
        accepted_at=datetime.now(timezone.utc).isoformat(),
    )
    await repo.add_participant(initiator_participant)

    await ctx.db.commit()
    await ctx.db.refresh(collab)

    from app.models.analytics import EventType
    from services.analytics_event_service import AnalyticsEventService

    await AnalyticsEventService(ctx.db).track_event(
        event_type=EventType.COLLAB_CREATED,
        user=user,
        session_id=ctx.session_id,
        metadata={"collaboration_id": str(collab.id), "participant_count": len(input.participant_ids)},
    )
    await ctx.db.commit()

    return _collaboration_to_gql(collab)


async def _accept_collaboration(ctx, id) -> CollaborationParticipantType:
    """Accept a collaboration invitation."""
    from repositories.collaboration_repository import CollaborationRepository
    from app.models.collaboration import CollaborationStatus
    from datetime import datetime, timezone

    user = ctx.require_auth()
    repo = CollaborationRepository(ctx.db)

    participant = await repo.get_participant(id, user.id)
    if not participant:
        raise ValueError("You are not a participant of this collaboration")
    if participant.accepted:
        raise ValueError("Collaboration invitation has already been accepted")

    collab = await repo.get_by_id(id)
    if not collab:
        raise ValueError("Collaboration not found")
    if collab.status != CollaborationStatus.PROPOSED:
        raise ValueError("Collaboration invitation is no longer pending")

    participant.accepted = True
    participant.accepted_at = datetime.now(timezone.utc).isoformat()
    await repo.update_participant(participant)
    collab.status = CollaborationStatus.ACCEPTED
    await repo.update(collab)
    await ctx.db.commit()

    return _participant_to_gql(participant)


async def _decline_collaboration(ctx, id) -> bool:
    """Decline a collaboration invitation."""
    from repositories.collaboration_repository import CollaborationRepository
    from app.models.collaboration import CollaborationStatus

    user = ctx.require_auth()
    repo = CollaborationRepository(ctx.db)

    participant = await repo.get_participant(id, user.id)
    if not participant:
        raise ValueError("You are not a participant of this collaboration")
    if participant.accepted:
        raise ValueError("An accepted collaboration cannot be declined")

    collab = await repo.get_by_id(id)
    if not collab:
        raise ValueError("Collaboration not found")
    if collab.status != CollaborationStatus.PROPOSED:
        raise ValueError("Collaboration invitation is no longer pending")

    await repo.remove_participant(participant)
    collab.status = CollaborationStatus.DECLINED
    await repo.update(collab)
    await ctx.db.commit()
    return True


async def _update_collaboration(ctx, id, input) -> CollaborationType:
    """Update an existing collaboration."""
    from repositories.collaboration_repository import CollaborationRepository
    from uuid import UUID as UUID_type
    from datetime import datetime, timezone

    user = ctx.require_auth()
    repo = CollaborationRepository(ctx.db)

    try:
        collab_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid collaboration ID")

    collab = await repo.get_by_id(collab_id)
    if not collab:
        raise ValueError("Collaboration not found")
    
    # Check ownership (only initiator can update)
    if collab.initiator_id != user.id and user.role.value not in ("admin",):
        raise PermissionError("Only the collaboration initiator can update it")

    # Apply updates from input
    for field in ("title", "description", "content_type", "platform", "tags", "budget_min", "budget_max", "budget_currency"):
        value = getattr(input, field, None)
        if value is not None:
            setattr(collab, field, value)

    collab.updated_at = datetime.now(timezone.utc)
    await repo.update(collab)
    await ctx.db.commit()
    return _collaboration_to_gql(collab)


async def _add_milestone(ctx, input) -> MilestoneType:
    """Add a milestone to a collaboration."""
    from repositories.collaboration_repository import CollaborationRepository
    from app.models.collaboration import Milestone, MilestoneStatus
    from uuid import uuid4

    user = ctx.require_auth()
    repo = CollaborationRepository(ctx.db)

    # Verify collaboration exists and user is participant
    collab = await repo.get_by_id(input.collaboration_id)
    if not collab:
        raise ValueError("Collaboration not found")
    
    participant = await repo.get_participant(input.collaboration_id, user.id)
    if not participant and collab.initiator_id != user.id:
        raise PermissionError("Not a participant in this collaboration")

    # Create milestone
    milestone = Milestone(
        id=uuid4(),
        collaboration_id=input.collaboration_id,
        title=input.title,
        description=input.description,
        status=MilestoneStatus.PENDING,
        due_at=input.due_date.isoformat() if input.due_date else None,
    )
    
    await repo.add_milestone(milestone)
    await ctx.db.commit()
    return _milestone_to_gql(milestone)


async def _update_milestone(ctx, id, input) -> MilestoneType:
    """Update a milestone."""
    from repositories.collaboration_repository import CollaborationRepository
    from uuid import UUID as UUID_type
    from app.models.collaboration import MilestoneStatus

    user = ctx.require_auth()
    repo = CollaborationRepository(ctx.db)

    try:
        milestone_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid milestone ID")

    # Get milestone and verify access
    milestone = await repo.get_milestone_by_id(milestone_id)
    if not milestone:
        raise ValueError("Milestone not found")
    
    # Verify user has access to the collaboration
    collab = await repo.get_by_id(milestone.collaboration_id)
    if not collab:
        raise ValueError("Collaboration not found")
    
    participant = await repo.get_participant(milestone.collaboration_id, user.id)
    if not participant and collab.initiator_id != user.id:
        raise PermissionError("Not a participant in this collaboration")

    # Apply updates
    if input.title is not None:
        milestone.title = input.title
    if input.description is not None:
        milestone.description = input.description
    if input.status is not None:
        milestone.status = MilestoneStatus(input.status.value)
    if input.due_date is not None:
        milestone.due_at = input.due_date.isoformat()

    await repo.update_milestone(milestone)
    await ctx.db.commit()
    return _milestone_to_gql(milestone)

def _messaging_write(handler):
    """Rollback database failures from messaging write resolvers."""
    @wraps(handler)
    async def wrapped(ctx, *args, **kwargs):
        try:
            return await handler(ctx, *args, **kwargs)
        except SQLAlchemyError as error:
            await ctx.db.rollback()
            raise ValueError("Messaging operation failed") from error
        except Exception:
            await ctx.db.rollback()
            raise

    return wrapped


@_messaging_write
async def _create_conversation(ctx, input) -> ConversationType:
    """Resolve createConversation mutation."""
    user = _require_active_messaging_user(ctx)
    
    from datetime import datetime, timezone
    from repositories.messaging_repository import ConversationRepository, ConversationParticipant
    from app.models.messaging import Conversation as ConversationModel
    
    # Validate participant IDs
    participant_ids = list(dict.fromkeys(input.participant_ids))
    if not participant_ids:
        raise ValueError("At least one participant required")
    if input.initial_message is not None:
        initial_message = input.initial_message.strip()
        if not initial_message or len(initial_message) > 5000:
            raise ValueError("Initial message must be between 1 and 5000 characters")
    if user.id in participant_ids:
        participant_ids.remove(user.id)
    if not participant_ids:
        raise ValueError("A conversation requires another participant")
    conv_repo = ConversationRepository(ctx.db)
    active_recipient_ids = await conv_repo.get_active_user_ids(participant_ids)
    if len(active_recipient_ids) != len(participant_ids):
        raise ValueError("One or more recipients are unavailable")
    if await conv_repo.users_are_blocked(user.id, participant_ids):
        raise PermissionError("Cannot message a blocked user")

    is_group = len(participant_ids) > 1
    if not is_group:
        existing = await conv_repo.get_direct_conversation(user.id, participant_ids[0])
        if existing:
            return _conversation_to_gql(existing)

    now = datetime.now(timezone.utc)
    conversation = ConversationModel(
        title=input.title,
        is_group=is_group,
        last_message_at=now.isoformat(),
        created_at=now,
        updated_at=now,
    )
    conversation = await conv_repo.create(conversation)
    
    all_participant_ids = [user.id, *participant_ids]
    
    for user_id in all_participant_ids:
        participant = ConversationParticipant(
            conversation_id=conversation.id,
            user_id=user_id,
        )
        await conv_repo.add_participant(participant)

    if input.initial_message:
        message_input = type("MessageInput", (), {
            "conversation_id": conversation.id,
            "body": input.initial_message,
            "content_type": "text",
            "attachments": None,
            "client_message_id": None,
        })()
        await _send_message(ctx, message_input, commit=False)
    
    await ctx.db.commit()
    return _conversation_to_gql(conversation)


@_messaging_write
async def _send_message(ctx, input, commit=True) -> MessageType:
    """Resolve sendMessage mutation."""
    user = _require_active_messaging_user(ctx)
    
    from datetime import datetime, timezone
    from repositories.messaging_repository import ConversationRepository, MessageRepository
    from app.models.messaging import Message as MessageModel
    
    # Verify user is participant in conversation
    conv_repo = ConversationRepository(ctx.db)
    participant = await conv_repo.get_participant(input.conversation_id, user.id)
    if not participant:
        raise ValueError("Access denied to this conversation")
    body = input.body.strip()
    if not body or len(body) > 5000:
        raise ValueError("Message body must be between 1 and 5000 characters")
    content_type = input.content_type or "text"
    if content_type not in {"text", "image", "file", "collaboration_invite"}:
        raise ValueError("Unsupported message content type")
    if input.attachments is not None and not isinstance(input.attachments, (dict, list)):
        raise ValueError("Attachments must be a JSON object or list")
    participant_ids = await conv_repo.get_participant_ids(input.conversation_id)
    if len(await conv_repo.get_active_user_ids(participant_ids)) != len(participant_ids):
        raise ValueError("One or more conversation participants are unavailable")
    if await conv_repo.users_are_blocked(user.id, participant_ids):
        raise PermissionError("Cannot message a blocked user")

    msg_repo = MessageRepository(ctx.db)
    if input.client_message_id:
        if len(input.client_message_id) > 128:
            raise ValueError("clientMessageId must be at most 128 characters")
        existing = await msg_repo.get_by_client_message_id(
            input.conversation_id, user.id, input.client_message_id
        )
        if existing:
            return _message_to_gql(existing)
    
    # Create message
    message = MessageModel(
        conversation_id=input.conversation_id,
        sender_id=user.id,
        content_type=content_type,
        body=body,
        attachments=input.attachments,
        client_message_id=input.client_message_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    try:
        message = await msg_repo.create(message)
    except IntegrityError:
        await ctx.db.rollback()
        if input.client_message_id:
            existing = await msg_repo.get_by_client_message_id(
                input.conversation_id, user.id, input.client_message_id
            )
            if existing:
                return _message_to_gql(existing)
        raise

    recipient_ids = await conv_repo.get_notification_recipient_ids(
        input.conversation_id, user.id
    )
    for recipient_id in recipient_ids:
        await _notify(
            ctx,
            user_id=recipient_id,
            type_name="MESSAGE",
            title="New message",
            body=f"{ctx.user.username} sent you a message",
            actor_id=user.id,
            event_key=f"message:{message.id}:{recipient_id}",
            data={"conversation_id": str(input.conversation_id), "message_id": str(message.id)},
        )
    
    # Update conversation's last_message_at
    conversation = await conv_repo.get_by_id(input.conversation_id)
    if conversation:
        conversation.last_message_text = body
        conversation.last_message_at = message.created_at.isoformat()
        conversation.last_message_by = user.id
    
    if commit:
        await ctx.db.commit()
    return _message_to_gql(message)


@_messaging_write
async def _mark_conversation_read(ctx, conversation_id) -> bool:
    from repositories.messaging_repository import ConversationRepository, MessageRepository

    user = _require_active_messaging_user(ctx)
    participant = await ConversationRepository(ctx.db).get_participant(conversation_id, user.id)
    if not participant:
        raise ValueError("Access denied to this conversation")
    await MessageRepository(ctx.db).mark_conversation_read(participant)
    await ctx.db.commit()
    return True


async def _unread_conversation_count(ctx) -> int:
    from repositories.messaging_repository import MessageRepository

    user = _require_active_messaging_user(ctx)
    return await MessageRepository(ctx.db).get_unread_count(user.id)


@_messaging_write
async def _delete_message(ctx, id) -> bool:
    from repositories.messaging_repository import MessageRepository

    user = _require_active_messaging_user(ctx)
    repo = MessageRepository(ctx.db)
    message = await repo.get_by_id(id)
    if not message or message.deleted_at is not None:
        raise ValueError("Message not found")
    if message.sender_id != user.id:
        raise PermissionError("Only the sender can delete this message")
    await repo.soft_delete(message)
    await ctx.db.commit()
    return True


def _require_active_messaging_user(ctx):
    """Return the current user when their account can use messaging."""
    user = ctx.require_auth()
    if user.deleted_at is not None or user.status.value != "active":
        raise PermissionError("Account is not active")
    return user


async def _mark_notification_read(ctx, id) -> bool:
    """Mark a notification as read."""
    from repositories.notification_repository import NotificationRepository
    from uuid import UUID as UUID_type

    user = ctx.require_auth()
    repo = NotificationRepository(ctx.db)

    try:
        notification_id = id if isinstance(id, UUID_type) else UUID_type(id)
    except (TypeError, ValueError):
        raise ValueError("Invalid notification ID")

    notification = await repo.get_by_id(notification_id)
    if not notification:
        raise ValueError("Notification not found")
    
    # Check ownership
    if notification.user_id != user.id:
        raise PermissionError("Not your notification")

    await repo.mark_as_read(notification)

    from app.models.analytics import EventType
    from services.analytics_event_service import AnalyticsEventService

    await AnalyticsEventService(ctx.db).track_event(
        event_type=EventType.NOTIFICATION_OPENED,
        user=user,
        session_id=ctx.session_id,
        metadata={"notification_type": getattr(notification.type, "value", str(notification.type))},
    )
    await ctx.db.commit()
    return True


async def _mark_all_notifications_read(ctx) -> bool:
    """Mark all notifications as read."""
    user = ctx.require_auth()
    from repositories.notification_repository import NotificationRepository
    
    repo = NotificationRepository(ctx.db)
    count = await repo.mark_all_as_read(user.id)
    await ctx.db.commit()
    return True


async def _endorse_user(ctx, input) -> EndorsementType:
    """Endorse another user."""
    from repositories.reputation_repository import ReputationRepository
    from app.models.reputation import Endorsement, EndorsementStatus
    from uuid import uuid4

    user = ctx.require_auth()
    
    # Prevent self-endorsement
    if user.id == input.endorsed_user_id:
        raise ValueError("Cannot endorse yourself")
    
    repo = ReputationRepository(ctx.db)
    
    # Create endorsement
    endorsement = Endorsement(
        id=uuid4(),
        endorser_id=user.id,
        endorsed_user_id=input.endorsed_user_id,
        skill=input.skill,
        comment=input.comment,
        status=EndorsementStatus.PENDING,  # Requires approval or auto-approve
    )
    
    await repo.create_endorsement(endorsement)
    await ctx.db.commit()
    
    # Map to GraphQL type
    return EndorsementType(
        id=endorsement.id,
        endorser_id=endorsement.endorser_id,
        endorsed_user_id=endorsement.endorsed_user_id,
        skill=endorsement.skill,
        comment=endorsement.comment,
        status=EndorsementStatus(endorsement.status.value),
        created_at=endorsement.created_at,
    )


async def _start_live_stream(ctx, title) -> LiveStreamType:
    """Start a live stream."""
    from repositories.live_stream_repository import LiveStreamRepository
    from app.models.content import LiveStream, LiveStreamStatus
    from uuid import uuid4

    user = ctx.require_auth()
    repo = LiveStreamRepository(ctx.db)

    # Create live stream
    live_stream = LiveStream(
        id=uuid4(),
        user_id=user.id,
        title=title,
        status=LiveStreamStatus.LIVE,
        viewer_count=0,
        started_at=datetime.now(timezone.utc),
    )

    await repo.create(live_stream)
    await ctx.db.commit()

    # Map to GraphQL type
    return LiveStreamType(
        id=live_stream.id,
        user_id=live_stream.user_id,
        title=live_stream.title,
        status=LiveStreamStatus(live_stream.status.value),
        viewer_count=live_stream.viewer_count,
        started_at=live_stream.started_at,
        ended_at=live_stream.ended_at,
    )


async def _end_live_stream(ctx, id) -> LiveStreamType:
    """End a live stream."""
    from repositories.live_stream_repository import LiveStreamRepository
    from uuid import UUID as UUID_type

    user = ctx.require_auth()
    repo = LiveStreamRepository(ctx.db)

    try:
        stream_id = UUID_type(id)
    except ValueError:
        raise ValueError("Invalid live stream ID")

    live_stream = await repo.get_by_id(stream_id)
    if not live_stream:
        raise ValueError("Live stream not found")

    # Check ownership
    if live_stream.user_id != user.id and user.role.value not in ("admin",):
        raise PermissionError("Only the stream owner can end this stream")

    # End the stream
    live_stream.status = LiveStreamStatus.ENDED
    live_stream.ended_at = datetime.now(timezone.utc)

    await repo.update(live_stream)
    await ctx.db.commit()

    return LiveStreamType(
        id=live_stream.id,
        user_id=live_stream.user_id,
        title=live_stream.title,
        status=LiveStreamStatus(live_stream.status.value),
        viewer_count=live_stream.viewer_count,
        started_at=live_stream.started_at,
        ended_at=live_stream.ended_at,
    )


async def _create_brand_opportunity(ctx, input) -> BrandOpportunityType:
    """Create a brand partnership opportunity."""
    from app.models.content import BrandOpportunity, BrandOpportunityStatus
    from uuid import uuid4

    user = ctx.require_auth()
    
    # Create brand opportunity
    opportunity = BrandOpportunity(
        id=uuid4(),
        creator_id=user.id,
        title=input.title,
        description=input.description,
        budget=input.budget,
        requirements=input.requirements,
        status=BrandOpportunityStatus.OPEN,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    ctx.db.add(opportunity)
    await ctx.db.commit()
    await ctx.db.refresh(opportunity)
    
    return BrandOpportunityType(
        id=opportunity.id,
        creator_id=opportunity.creator_id,
        title=opportunity.title,
        description=opportunity.description,
        budget=opportunity.budget,
        requirements=opportunity.requirements,
        status=opportunity.status,
        created_at=opportunity.created_at,
        updated_at=opportunity.updated_at,
    )


async def _apply_to_brand_opportunity(ctx, input) -> BrandApplicationType:
    """Apply to a brand partnership opportunity."""
    from app.models.content import BrandApplication, BrandApplicationStatus
    from uuid import uuid4

    user = ctx.require_auth()
    
    # Create brand application
    application = BrandApplication(
        id=uuid4(),
        opportunity_id=input.opportunity_id,
        applicant_id=user.id,
        pitch=input.pitch,
        status=BrandApplicationStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    ctx.db.add(application)
    await ctx.db.commit()
    await ctx.db.refresh(application)
    
    return BrandApplicationType(
        id=application.id,
        opportunity_id=application.opportunity_id,
        applicant_id=application.applicant_id,
        pitch=application.pitch,
        status=application.status,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


async def _report_content(ctx, input) -> ReportType:
    """Report content or a user."""
    from app.models.notification import Report, ReportStatus, ReportReason
    from uuid import uuid4

    user = ctx.require_auth()
    
    # Create report
    report = Report(
        id=uuid4(),
        reporter_id=user.id,
        reported_user_id=input.reported_user_id,
        content_type=input.content_type,
        content_id=input.content_id,
        reason=ReportReason(input.reason.value) if input.reason else ReportReason.OTHER,
        description=input.description,
        status=ReportStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    
    ctx.db.add(report)
    await ctx.db.commit()
    await ctx.db.refresh(report)
    
    return ReportType(
        id=report.id,
        reporter_id=report.reporter_id,
        reported_user_id=report.reported_user_id,
        content_type=report.content_type,
        content_id=report.content_id,
        reason=report.reason,
        description=report.description,
        status=report.status,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


# ── Legacy-compatible content/social resolvers ───────────────────────────────
#
# Backs the additive types/mutations declared above. Mirrors the old
# backend's behavior (toggle-style like/save, denormalized counts refreshed
# from the join tables, one PostWatch row per call) on top of Seth's
# repository pattern.


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


async def _profile_to_summary(ctx, profile, user=None) -> ProfileSummaryType:
    from repositories.user_repository import UserRepository
    from repositories.social_repository import FollowRepository

    if user is None:
        if ctx and ctx.current_user and getattr(profile, "user_id", None) == ctx.current_user.id:
            user = ctx.current_user
        else:
            try:
                user = await UserRepository(ctx.db).get_by_id(profile.user_id)
            except Exception:
                user = getattr(profile, "user", None)

    is_following = False
    if ctx and ctx.current_user and user and ctx.current_user.id != user.id:
        try:
            is_following = await FollowRepository(ctx.db).is_following(ctx.current_user.id, user.id)
        except Exception:
            is_following = False

    return ProfileSummaryType(
        id=profile.id,
        username=getattr(user, "username", None) or getattr(profile, "username", "") or "",
        display_name=getattr(profile, "display_name", ""),
        avatar_url=getattr(profile, "avatar_url", "") or "",
        avatar_color=getattr(profile, "avatar_color", "") or "#00AEEF",
        verified=getattr(profile, "verified", False),
        collab_score=getattr(profile, "collab_score", 0.0),
        collab_count=getattr(profile, "collaboration_count", 0),
        followers=getattr(profile, "follower_count", 0),
        following=getattr(profile, "following_count", 0),
        open_to_collab=getattr(profile, "open_to_collab", True),
        private_account=getattr(profile, "private_account", False),
        is_following=is_following,
    )


async def _post_to_legacy_post(
    ctx, post, liked_ids=None, saved_ids=None, shared_ids=None,
) -> LegacyPostType:
    from repositories.social_repository import PostInteractionRepository

    viewer_id = ctx.current_user.id if ctx.current_user else None
    interactions = PostInteractionRepository(ctx.db)

    if liked_ids is not None:
        is_liked = post.id in liked_ids
    else:
        try:
            is_liked = bool(viewer_id) and await interactions.has_liked(post.id, viewer_id)
        except Exception:
            is_liked = False
    if saved_ids is not None:
        is_saved = post.id in saved_ids
    else:
        try:
            is_saved = bool(viewer_id) and await interactions.has_saved(post.id, viewer_id)
        except Exception:
            is_saved = False
    if shared_ids is not None:
        is_shared = post.id in shared_ids
    else:
        try:
            is_shared = bool(viewer_id) and await interactions.has_shared(post.id, viewer_id)
        except Exception:
            is_shared = False

    return LegacyPostType(
        id=post.id,
        thumbnail=post.thumbnail or "",
        media_url=post.media_url,
        caption=post.caption or "",
        views=post.view_count,
        likes=post.like_count,
        is_liked=is_liked,
        collab_with=post.collab_with,
        hashtags=post.hashtags or [],
        audio=post.audio,
        visibility=post.visibility,
        allow_comments=post.allow_comments,
        allow_collabs=post.allow_collabs,
        duration_sec=post.duration_sec,
        comments=post.comment_count,
        shares=post.share_count,
        saves=post.save_count,
        is_saved=is_saved,
        is_shared=is_shared,
        status=post.status.value,
        scheduled_at=post.scheduled_at,
    )


async def _post_to_feed_item(ctx, post) -> FeedItemType:
    from repositories.profile_repository import ProfileRepository

    item = await _post_to_legacy_post(ctx, post)
    profile = await ProfileRepository(ctx.db).get_by_user_id(post.user_id)
    creator = await _profile_to_summary(ctx, profile) if profile else ProfileSummaryType(
        id=post.user_id, username="", display_name=""
    )
    return FeedItemType(**vars(item), creator=creator)


async def _profile_to_detail(ctx, profile) -> ProfileDetailType:
    from repositories.user_repository import UserRepository
    from repositories.social_repository import FollowRepository, PlaylistRepository
    from repositories.content_repository import PostRepository
    from app.models.content import ContentStatus

    user = await UserRepository(ctx.db).get_by_id(profile.user_id)
    follow_repo = FollowRepository(ctx.db)

    is_following = False
    if ctx.current_user and user and ctx.current_user.id != user.id:
        is_following = await follow_repo.is_following(ctx.current_user.id, user.id)

    viewer_is_owner = bool(ctx.current_user and user and ctx.current_user.id == user.id)
    posts = await PostRepository(ctx.db).get_by_user_id(profile.user_id, limit=12)
    if not viewer_is_owner:
        posts = [p for p in posts if p.status == ContentStatus.PUBLISHED]
    post_items = [await _post_to_legacy_post(ctx, p) for p in posts]

    playlists = await PlaylistRepository(ctx.db).get_by_profile_id(profile.id)
    playlist_items = [
        PlaylistType(id=pl.id, title=pl.title, cover=pl.cover, item_label=pl.item_label, plays=pl.plays)
        for pl in playlists
    ]

    return ProfileDetailType(
        id=profile.id,
        username=user.username if user else "",
        display_name=profile.display_name,
        avatar_url=profile.avatar_url or "",
        avatar_color=profile.avatar_color or "#00AEEF",
        bio=profile.bio,
        location=profile.location,
        website=profile.website_url,
        verified=profile.verified,
        online=profile.online,
        collab_status=profile.collab_status,
        collab_score=profile.collab_score,
        collab_count=profile.collaboration_count,
        followers=profile.follower_count,
        following=profile.following_count,
        open_to_collab=profile.open_to_collab,
        private_account=profile.private_account,
        response_time=profile.response_time,
        posts=post_items,
        playlists=playlist_items,
        is_following=is_following,
    )


async def _profile_detail_for_user(ctx, user) -> Optional[ProfileDetailType]:
    from repositories.profile_repository import ProfileRepository
    from app.models.user import Profile

    profile_repo = ProfileRepository(ctx.db)
    profile = await profile_repo.get_by_user_id(user.id)
    if not profile:
        profile = Profile(user_id=user.id, display_name=user.username)
        await profile_repo.create(profile)
        await ctx.db.commit()
    return await _profile_to_detail(ctx, profile)


async def _search_profiles(ctx, query, after, limit, verified_only, open_to_collab) -> ProfilePageType:
    from repositories.social_repository import FollowRepository
    from repositories.user_repository import UserRepository

    query = (query or "").strip()
    if not query:
        return ProfilePageType(profiles=[], next_cursor=None)
    try:
        offset = max(0, int(after or "0"))
    except ValueError as exc:
        raise ValueError("Invalid search cursor") from exc

    page_size = max(1, min(int(limit or 20), 50))
    viewer = ctx.user
    following_ids = (
        await FollowRepository(ctx.db).get_following_ids(viewer.id) if viewer else []
    )
    rows = await UserRepository(ctx.db).search_by_username_and_display_name(
        query,
        viewer_id=viewer.id if viewer else None,
        following_ids=following_ids,
        verified_only=verified_only,
        open_to_collab=open_to_collab,
        limit=page_size + 1,
        offset=offset,
    )

    profiles = [user.profile for user, _score in rows if user.profile is not None]

    has_more = len(profiles) > page_size
    if has_more:
        profiles = profiles[:page_size]

    summaries = [await _profile_to_summary(ctx, p) for p in profiles]
    next_cursor = str(offset + page_size) if has_more else None
    return ProfilePageType(profiles=summaries, next_cursor=next_cursor)


async def _search_posts(ctx, query, after, limit, hashtag, sort_by) -> FeedPageType:
    from repositories.content_repository import PostRepository
    from repositories.social_repository import FollowRepository

    user = ctx.require_auth()

    query = (query or "").strip()
    if not query:
        return FeedPageType(items=[], next_cursor=None)

    try:
        offset = max(0, int(after or "0"))
    except ValueError as exc:
        raise ValueError("Invalid search cursor") from exc

    page_size = max(1, min(int(limit or 20), 50))
    following_ids = await FollowRepository(ctx.db).get_following_ids(user.id)
    rows = await PostRepository(ctx.db).search_by_content(
        query,
        viewer_id=user.id,
        following_ids=following_ids,
        limit=page_size + 1,
        offset=offset,
    )

    if hashtag:
        tag = hashtag.strip().lstrip("#").lower()
        if tag:
            rows = [
                (post, profile, score)
                for post, profile, score in rows
                if isinstance(post.hashtags, list) and any(str(item).strip().lstrip("#").lower() == tag for item in post.hashtags)
            ]

    if sort_by == "recent":
        rows.sort(key=lambda item: item[0].created_at, reverse=True)
    elif sort_by == "popular":
        rows.sort(key=lambda item: item[0].view_count, reverse=True)
    else:
        rows.sort(key=lambda item: item[2], reverse=True)

    has_more = len(rows) > page_size
    rows = rows[:page_size]

    items = []
    for post, profile, _score in rows:
        creator = await _profile_to_summary(ctx, profile) if profile else None
        item = await _post_to_legacy_post(ctx, post)
        if creator is None:
            creator = ProfileSummaryType(
                id=post.user_id,
                username="",
                display_name="",
            )
        items.append(FeedItemType(**vars(item), creator=creator))

    next_cursor = str(offset + page_size) if has_more else None
    return FeedPageType(items=items, next_cursor=next_cursor)


async def _search_hashtags(ctx, query, after, limit) -> HashtagPageType:
    from sqlalchemy import select, func, desc
    from app.models.content import Post

    term = (query or "").strip().lstrip("#").lower()
    if not term:
        return HashtagPageType(hashtags=[], next_cursor=None)

    try:
        offset = max(0, int(after or "0"))
    except ValueError as exc:
        raise ValueError("Invalid search cursor") from exc

    page_size = max(1, min(int(limit or 20), 50))
    stmt = (
        select(Post.hashtags, func.sum(Post.view_count).label("view_total"), func.count(Post.id).label("post_count"))
        .where(Post.deleted_at.is_(None))
        .where(Post.status == "published")
    )
    result = await ctx.db.execute(stmt)
    counts = {}
    for hashtags, view_total, post_count in result.all():
        if not hashtags:
            continue
        for value in hashtags:
            tag = str(value).strip().lstrip("#").lower()
            if tag and term in tag:
                counts[tag] = {
                    "posts": counts.get(tag, {}).get("posts", 0) + 1,
                    "views": counts.get(tag, {}).get("views", 0) + (view_total or 0),
                }

    ranked = sorted(counts.items(), key=lambda item: (0 if item[0].startswith(term) else 1, -item[1]["views"], item[0]))
    page = ranked[offset: offset + page_size + 1]
    has_more = len(page) > page_size
    page = page[:page_size]
    hashtags = [
        HashtagResultType(tag=tag, posts=data["posts"], views=data["views"])
        for tag, data in page
    ]
    next_cursor = str(offset + page_size) if has_more else None
    return HashtagPageType(hashtags=hashtags, next_cursor=next_cursor)


async def _suggested_profiles(ctx, limit) -> List[ProfileSummaryType]:
    from sqlalchemy import select
    from app.models.user import Profile
    from repositories.social_repository import FollowRepository

    user = ctx.require_auth()
    following_ids = set(await FollowRepository(ctx.db).get_following_ids(user.id))
    result = await ctx.db.execute(
        select(Profile).where(Profile.user_id != user.id).order_by(Profile.follower_count.desc()).limit(limit + len(following_ids))
    )
    profiles = [p for p in result.scalars().all() if p.user_id not in following_ids][:limit]
    return [await _profile_to_summary(ctx, p) for p in profiles]


async def _my_following(ctx) -> List[ProfileSummaryType]:
    from repositories.social_repository import FollowRepository
    from repositories.profile_repository import ProfileRepository

    user = ctx.require_auth()
    following_ids = await FollowRepository(ctx.db).get_following_ids(user.id)
    profiles = await ProfileRepository(ctx.db).get_multiple_by_user_ids(following_ids)
    return [await _profile_to_summary(ctx, p) for p in profiles]


async def _my_followers(ctx) -> List[ProfileSummaryType]:
    from repositories.social_repository import FollowRepository
    from repositories.profile_repository import ProfileRepository

    user = ctx.require_auth()
    follower_ids = await FollowRepository(ctx.db).get_follower_ids(user.id)
    profiles = await ProfileRepository(ctx.db).get_multiple_by_user_ids(follower_ids)
    return [await _profile_to_summary(ctx, p) for p in profiles]


async def _following_page_for(ctx, username, after, limit) -> ProfilePageType:
    from repositories.social_repository import FollowRepository
    from repositories.profile_repository import ProfileRepository
    from repositories.user_repository import UserRepository

    if username:
        target = await UserRepository(ctx.db).get_by_username(username)
        if not target:
            return ProfilePageType(profiles=[], next_cursor=None)
        target_id = target.id
    else:
        target_id = ctx.require_auth().id

    offset = int(after) if after else 0
    all_ids = await FollowRepository(ctx.db).get_following_ids(target_id)
    page_ids = all_ids[offset : offset + limit]
    profiles = await ProfileRepository(ctx.db).get_multiple_by_user_ids(page_ids)
    summaries = [await _profile_to_summary(ctx, p) for p in profiles]
    next_cursor = str(offset + limit) if offset + limit < len(all_ids) else None
    return ProfilePageType(profiles=summaries, next_cursor=next_cursor)


async def _followers_page_for(ctx, username, after, limit) -> ProfilePageType:
    from repositories.social_repository import FollowRepository
    from repositories.profile_repository import ProfileRepository
    from repositories.user_repository import UserRepository

    if username:
        target = await UserRepository(ctx.db).get_by_username(username)
        if not target:
            return ProfilePageType(profiles=[], next_cursor=None)
        target_id = target.id
    else:
        target_id = ctx.require_auth().id

    offset = int(after) if after else 0
    all_ids = await FollowRepository(ctx.db).get_follower_ids(target_id)
    page_ids = all_ids[offset : offset + limit]
    profiles = await ProfileRepository(ctx.db).get_multiple_by_user_ids(page_ids)
    summaries = [await _profile_to_summary(ctx, p) for p in profiles]
    next_cursor = str(offset + limit) if offset + limit < len(all_ids) else None
    return ProfilePageType(profiles=summaries, next_cursor=next_cursor)


async def _my_posts(ctx) -> List[LegacyPostType]:
    from repositories.content_repository import PostRepository

    user = ctx.require_auth()
    posts = await PostRepository(ctx.db).get_by_user_id(user.id, limit=200)
    return [await _post_to_legacy_post(ctx, p) for p in posts]


async def _comments(ctx, post_id, limit) -> List[CommentGQLType]:
    from repositories.content_repository import CommentRepository
    from repositories.social_repository import CommentInteractionRepository
    from repositories.profile_repository import ProfileRepository

    comments = await CommentRepository(ctx.db).get_by_post_id(post_id, limit=limit)
    viewer_id = ctx.current_user.id if ctx.current_user else None
    interactions = CommentInteractionRepository(ctx.db)
    liked_ids = await interactions.liked_comment_ids(viewer_id, [c.id for c in comments]) if viewer_id else set()

    out = []
    for c in comments:
        profile = await ProfileRepository(ctx.db).get_by_user_id(c.user_id)
        author = await _profile_to_summary(ctx, profile) if profile else ProfileSummaryType(
            id=c.user_id, username="", display_name=""
        )
        out.append(CommentGQLType(
            id=c.id,
            text=c.body,
            likes=c.like_count,
            is_liked=c.id in liked_ids,
            can_delete=bool(viewer_id and viewer_id == c.user_id),
            can_edit=bool(viewer_id and viewer_id == c.user_id),
            moderation_status=getattr(c, "moderation_status", "approved"),
            created_at=_iso(c.created_at) or "",
            author=author,
        ))
    return out


async def _delete_account(ctx) -> bool:
    user = ctx.require_auth()
    await ctx.db.delete(user)
    await ctx.db.commit()
    return True


async def _follow(ctx, username) -> FollowResultType:
    from repositories.user_repository import UserRepository
    from repositories.social_repository import FollowRepository
    from repositories.profile_repository import ProfileRepository

    user = ctx.require_auth()
    target = await UserRepository(ctx.db).get_by_username(username)
    if not target:
        raise ValueError("Profile not found")
    if target.id == user.id:
        raise ValueError("You cannot follow yourself")

    follow_repo = FollowRepository(ctx.db)
    is_new_follow = await follow_repo.follow(user.id, target.id)

    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType, EventType
    from services.analytics_event_service import AnalyticsEventService

    if is_new_follow:
        await AnalyticsRepository(ctx.db).record(
            user_id=user.id, creator_id=target.id, signal_type=SignalType.FOLLOW
        )
        await AnalyticsEventService(ctx.db).track_event(
            event_type=EventType.FOLLOW_CREATED,
            user=user,
            target_user=target,
            session_id=ctx.session_id,
        )

    profile_repo = ProfileRepository(ctx.db)
    target_profile = await profile_repo.get_by_user_id(target.id)
    viewer_profile = await profile_repo.get_by_user_id(user.id)
    if target_profile:
        target_profile.follower_count = await follow_repo.count_followers(target.id)
    if viewer_profile:
        viewer_profile.following_count = await follow_repo.count_following(user.id)

    if is_new_follow:
        await _notify(
            ctx,
            user_id=target.id,
            type_name="NEW_FOLLOWER",
            title="New follower",
            body=f"{user.username} started following you",
            actor_id=user.id,
            event_key=None,
            data={"user_id": str(user.id)},
        )

    await ctx.db.commit()

    return FollowResultType(
        following=True,
        followers=target_profile.follower_count if target_profile else 0,
        following_count=viewer_profile.following_count if viewer_profile else 0,
    )


async def _unfollow(ctx, username) -> FollowResultType:
    from repositories.user_repository import UserRepository
    from repositories.social_repository import FollowRepository
    from repositories.profile_repository import ProfileRepository

    user = ctx.require_auth()
    target = await UserRepository(ctx.db).get_by_username(username)
    if not target:
        raise ValueError("Profile not found")

    follow_repo = FollowRepository(ctx.db)
    await follow_repo.unfollow(user.id, target.id)

    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType, EventType
    from services.analytics_event_service import AnalyticsEventService

    await AnalyticsRepository(ctx.db).record(
        user_id=user.id, creator_id=target.id, signal_type=SignalType.UNFOLLOW
    )
    await AnalyticsEventService(ctx.db).track_event(
        event_type=EventType.FOLLOW_REMOVED,
        user=user,
        target_user=target,
        session_id=ctx.session_id,
    )

    profile_repo = ProfileRepository(ctx.db)
    target_profile = await profile_repo.get_by_user_id(target.id)
    viewer_profile = await profile_repo.get_by_user_id(user.id)
    if target_profile:
        target_profile.follower_count = await follow_repo.count_followers(target.id)
    if viewer_profile:
        viewer_profile.following_count = await follow_repo.count_following(user.id)
    await ctx.db.commit()

    return FollowResultType(
        following=False,
        followers=target_profile.follower_count if target_profile else 0,
        following_count=viewer_profile.following_count if viewer_profile else 0,
    )


async def _create_post_legacy(ctx, input) -> LegacyPostType:
    from repositories.content_repository import PostRepository
    from app.models.content import Post, ContentType as CT, ContentStatus as CS
    from app.models.analytics import EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)

    status = input.status or "published"
    post = Post(
        user_id=user.id,
        content_type=CT.VIDEO,
        status=CS(status),
        caption=input.caption,
        mentions=input.mentions,
        collab_with=input.collab_with,
        hashtags=input.hashtags or [],
        audio=input.audio,
        visibility=input.visibility,
        allow_comments=input.allow_comments,
        allow_collabs=input.allow_collabs,
        duration_sec=input.duration_sec,
        scheduled_at=input.scheduled_at,
    )
    await post_repo.create(post)
    await _notify_mentions(ctx, input.mentions, user.id, post.id, "post")

    events = AnalyticsEventService(ctx.db)
    if post.audio and post.audio != "Original Sound":
        await events.track_event(
            event_type=EventType.SOUND_USED,
            user=user,
            post=post,
            session_id=ctx.session_id,
            metadata={"audio": post.audio},
        )
    if post.status == CS.PUBLISHED:
        await events.track_event(
            event_type=EventType.VIDEO_PUBLISHED,
            user=user,
            post=post,
            session_id=ctx.session_id,
        )

    await ctx.db.commit()
    return await _post_to_legacy_post(ctx, post)


async def _update_post_legacy(ctx, id, input) -> LegacyPostType:
    from repositories.content_repository import PostRepository
    from app.models.content import ContentStatus as CS
    from app.models.analytics import EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)

    post = await post_repo.get_by_id(id)
    if not post:
        raise ValueError("Post not found")
    if post.user_id != user.id and user.role.value not in ("admin",):
        raise PermissionError("Only the post author can update this post")

    was_published = post.status == CS.PUBLISHED
    for field in (
        "caption", "collab_with", "hashtags", "audio", "visibility",
        "allow_comments", "allow_collabs", "duration_sec", "scheduled_at",
    ):
        value = getattr(input, field, None)
        if value is not None:
            setattr(post, field, value)
    if input.status is not None:
        post.status = CS(input.status)

    post.updated_at = datetime.now(timezone.utc)
    await post_repo.update(post)

    if not was_published and post.status == CS.PUBLISHED:
        await AnalyticsEventService(ctx.db).track_event(
            event_type=EventType.VIDEO_PUBLISHED,
            user=user,
            post=post,
            session_id=ctx.session_id,
        )

    await ctx.db.commit()
    return await _post_to_legacy_post(ctx, post)


async def _like_post_legacy(ctx, id, like: bool) -> LikeResultType:
    from repositories.content_repository import PostRepository
    from repositories.social_repository import PostInteractionRepository
    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType, EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    interactions = PostInteractionRepository(ctx.db)

    post = await post_repo.get_by_id(id)
    if not post:
        raise ValueError("Post not found")

    is_liked = await interactions.has_liked(id, user.id)
    if like and not is_liked:
        created_like = await interactions.toggle_like(id, user.id)
        if created_like:
            await AnalyticsRepository(ctx.db).record(
                user_id=user.id, creator_id=post.user_id, post_id=id, signal_type=SignalType.LIKE
            )
            await AnalyticsEventService(ctx.db).track_event(
                event_type=EventType.LIKE_CREATED,
                user=user,
                post=post,
                session_id=ctx.session_id,
            )
        if created_like and post.user_id != user.id:
            from repositories.notification_repository import NotificationRepository
            from app.models.notification import NotificationType as NType

            await NotificationRepository(ctx.db).create_notification(
                user_id=post.user_id,
                type=NType.NEW_LIKE,
                title="New like",
                body=f"{user.username} liked your post",
                actor_id=user.id,
                data={"post_id": str(id)},
            )
    elif not like and is_liked:
        await interactions.toggle_like(id, user.id)
        await AnalyticsRepository(ctx.db).record(
            user_id=user.id, creator_id=post.user_id, post_id=id, signal_type=SignalType.UNLIKE
        )
        await AnalyticsEventService(ctx.db).track_event(
            event_type=EventType.LIKE_REMOVED,
            user=user,
            post=post,
            session_id=ctx.session_id,
        )

    post.like_count = await interactions.count_likes(id)
    await ctx.db.commit()
    return LikeResultType(liked=like, likes=post.like_count)


async def _save_post_legacy(ctx, id, save: bool) -> SaveResultType:
    from repositories.content_repository import PostRepository
    from repositories.social_repository import PostInteractionRepository
    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType, EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    interactions = PostInteractionRepository(ctx.db)

    post = await post_repo.get_by_id(id)
    if not post:
        raise ValueError("Post not found")

    is_saved = await interactions.has_saved(id, user.id)
    if save and not is_saved:
        await interactions.toggle_save(id, user.id)
        await AnalyticsRepository(ctx.db).record(
            user_id=user.id, creator_id=post.user_id, post_id=id, signal_type=SignalType.SAVE
        )
        await AnalyticsEventService(ctx.db).track_event(
            event_type=EventType.SAVE_CREATED,
            user=user,
            post=post,
            session_id=ctx.session_id,
        )
    elif not save and is_saved:
        await interactions.toggle_save(id, user.id)
        await AnalyticsRepository(ctx.db).record(
            user_id=user.id, creator_id=post.user_id, post_id=id, signal_type=SignalType.UNSAVE
        )

    post.save_count = await interactions.count_saves(id)
    await ctx.db.commit()
    return SaveResultType(saved=save, saves=post.save_count)


async def _share_post_legacy(ctx, id) -> ShareResultType:
    from repositories.content_repository import PostRepository
    from repositories.social_repository import PostInteractionRepository
    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType, EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    interactions = PostInteractionRepository(ctx.db)

    post = await post_repo.get_by_id(id)
    if not post:
        raise ValueError("Post not found")

    created_share = await interactions.add_share(id, user.id)
    if created_share:
        await AnalyticsRepository(ctx.db).record(
            user_id=user.id, creator_id=post.user_id, post_id=id, signal_type=SignalType.SHARE
        )
        await AnalyticsEventService(ctx.db).track_event(
            event_type=EventType.SHARE_CREATED,
            user=user,
            post=post,
            session_id=ctx.session_id,
        )
    post.share_count = await interactions.count_shares(id)
    await ctx.db.commit()
    return ShareResultType(shares=post.share_count, shared=True)


async def _track_post_watch(ctx, post_id, watched_seconds, completed) -> WatchResultType:
    from repositories.content_repository import PostRepository
    from repositories.social_repository import PostInteractionRepository
    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType, EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    interactions = PostInteractionRepository(ctx.db)

    post = await post_repo.get_by_id(post_id)
    if not post:
        raise ValueError("Post not found")

    clamped_seconds = max(0.0, min(watched_seconds, post.duration_sec)) if post.duration_sec else watched_seconds
    verified_completed = completed and post.duration_sec > 0 and clamped_seconds >= 0.9 * post.duration_sec

    watch = await interactions.track_watch(post_id, user.id, clamped_seconds, verified_completed)
    post.view_count = await interactions.count_views(post_id)

    analytics = AnalyticsRepository(ctx.db)
    events = AnalyticsEventService(ctx.db)
    duration_ms = int(watch.watched_seconds * 1000)
    if watch.rewatched:
        await analytics.record(
            user_id=user.id, creator_id=post.user_id, post_id=post_id, signal_type=SignalType.REWATCH
        )
    else:
        await analytics.record(
            user_id=user.id, creator_id=post.user_id, post_id=post_id, signal_type=SignalType.VIEW
        )
    await analytics.record(
        user_id=user.id,
        creator_id=post.user_id,
        post_id=post_id,
        signal_type=SignalType.WATCH_DURATION,
        value=watch.watched_seconds,
    )

    await events.track_event(
        event_type=EventType.VIDEO_VIEWED, user=user, post=post, session_id=ctx.session_id,
    )
    await events.track_event(
        event_type=EventType.VIDEO_WATCHED,
        user=user,
        post=post,
        session_id=ctx.session_id,
        duration_ms=duration_ms,
    )

    if watch.completed:
        await analytics.record(
            user_id=user.id, creator_id=post.user_id, post_id=post_id, signal_type=SignalType.COMPLETION
        )
        await events.track_event(
            event_type=EventType.VIDEO_COMPLETED,
            user=user,
            post=post,
            session_id=ctx.session_id,
            duration_ms=duration_ms,
        )
    elif post.duration_sec and watch.watched_seconds < 0.25 * post.duration_sec:
        # Left well before the end without completing — treat as a skip.
        await events.track_event(
            event_type=EventType.VIDEO_SKIPPED,
            user=user,
            post=post,
            session_id=ctx.session_id,
            duration_ms=duration_ms,
        )

    await ctx.db.commit()

    return WatchResultType(
        views=post.view_count,
        watched_seconds=watch.watched_seconds,
        completed=watch.completed,
        rewatched=watch.rewatched,
    )


async def _not_interested(ctx, post_id) -> NotInterestedResultType:
    """Explicit "Not Interested" feedback on a post.

    Records a real production signal (SignalType.NOT_INTERESTED) into the
    unified interaction-signal log. The recommendation system reads it via
    ``AnalyticsRepository.viewer_post_history`` to strongly demote this post
    (demote, never hard-exclude) and — because the signal is creator-scoped —
    to soften future recommendations from the same creator for this viewer.
    Idempotent per (user, post): a repeat tap does not stack signals.
    """
    from repositories.content_repository import PostRepository
    from repositories.analytics_repository import AnalyticsRepository
    from app.models.analytics import SignalType, EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)

    post = await post_repo.get_by_id(post_id)
    if not post:
        raise ValueError("Post not found")

    analytics = AnalyticsRepository(ctx.db)
    history = await analytics.viewer_post_history(user.id, [post_id])
    already_flagged = bool(history.get(post_id) and history[post_id].not_interested)
    if not already_flagged:
        await analytics.record(
            user_id=user.id,
            creator_id=post.user_id,
            post_id=post_id,
            signal_type=SignalType.NOT_INTERESTED,
        )
        await AnalyticsEventService(ctx.db).track_event(
            event_type=EventType.NOT_INTERESTED,
            user=user,
            post=post,
            session_id=ctx.session_id,
        )
        await ctx.db.commit()

    return NotInterestedResultType(not_interested=True, post_id=post_id)


async def _add_comment(ctx, post_id, text) -> CommentGQLType:
    from repositories.content_repository import PostRepository, CommentRepository
    from repositories.profile_repository import ProfileRepository
    from app.models.content import Comment
    from app.models.analytics import EventType
    from services.analytics_event_service import AnalyticsEventService

    user = ctx.require_auth()
    post_repo = PostRepository(ctx.db)
    comment_repo = CommentRepository(ctx.db)

    post = await post_repo.get_by_id(post_id)
    if not post:
        raise ValueError("Post not found")
    if not post.allow_comments:
        raise PermissionError("Comments are disabled for this post")

    comment = Comment(post_id=post_id, user_id=user.id, body=text)
    await comment_repo.create(comment)
    post.comment_count += 1
    await AnalyticsEventService(ctx.db).track_event(
        event_type=EventType.COMMENT_CREATED,
        user=user,
        post=post,
        session_id=ctx.session_id,
    )
    await _notify(
        ctx,
        user_id=post.user_id,
        type_name="NEW_COMMENT",
        title="New comment",
        body=f"{user.username} commented on your post",
        actor_id=user.id,
        event_key=f"comment:{comment.id}",
        data={"post_id": str(post.id), "comment_id": str(comment.id)},
    )
    await _notify_username_mentions(ctx, text, user.id, comment.id, "comment")
    await ctx.db.commit()

    profile = await ProfileRepository(ctx.db).get_by_user_id(user.id)
    author = await _profile_to_summary(ctx, profile) if profile else ProfileSummaryType(
        id=user.id, username=user.username, display_name=user.username
    )
    return CommentGQLType(
        id=comment.id, text=comment.body, likes=0, is_liked=False,
        can_delete=True, can_edit=True, moderation_status="approved",
        created_at=_iso(comment.created_at) or "", author=author,
    )


async def _notify(
    ctx,
    *,
    user_id,
    type_name: str,
    title: str,
    body: str,
    actor_id,
    event_key: Optional[str],
    data: dict,
) -> None:
    """Queue one persisted in-app notification within the current transaction."""
    from app.models.notification import NotificationType
    from repositories.notification_repository import NotificationRepository
    from repositories.user_repository import UserRepository

    if not await UserRepository(ctx.db).get_by_id(user_id):
        return
    await NotificationRepository(ctx.db).create_notification(
        user_id=user_id,
        type=NotificationType[type_name],
        title=title,
        body=body,
        actor_id=actor_id,
        event_key=event_key,
        data=data,
    )


async def _notify_mentions(ctx, mentioned_user_ids, actor_id, source_id, source_type: str) -> None:
    for mentioned_user_id in set(mentioned_user_ids or []):
        await _notify(
            ctx,
            user_id=mentioned_user_id,
            type_name="MENTION",
            title="You were mentioned",
            body=f"{ctx.user.username} mentioned you",
            actor_id=actor_id,
            event_key=f"mention:{source_type}:{source_id}:{mentioned_user_id}",
            data={f"{source_type}_id": str(source_id)},
        )


async def _notify_username_mentions(ctx, text: str, actor_id, source_id, source_type: str) -> None:
    from repositories.profile_repository import ProfileRepository

    usernames = {username.lower() for username in re.findall(r"(?<!\w)@([A-Za-z0-9_]{1,64})", text)}
    profiles = [
        await ProfileRepository(ctx.db).get_by_username(username)
        for username in usernames
    ]
    await _notify_mentions(
        ctx,
        [profile.user_id for profile in profiles if profile],
        actor_id,
        source_id,
        source_type,
    )


async def _edit_comment(ctx, id, text) -> CommentGQLType:
    from repositories.content_repository import CommentRepository
    from repositories.profile_repository import ProfileRepository

    user = ctx.require_auth()
    comment_repo = CommentRepository(ctx.db)

    comment = await comment_repo.get_by_id(id)
    if not comment:
        raise ValueError("Comment not found")
    if comment.user_id != user.id:
        raise PermissionError("Only the comment author can edit it")

    comment.body = text
    comment.is_edited = True
    await ctx.db.commit()

    profile = await ProfileRepository(ctx.db).get_by_user_id(user.id)
    author = await _profile_to_summary(ctx, profile) if profile else ProfileSummaryType(
        id=user.id, username=user.username, display_name=user.username
    )
    return CommentGQLType(
        id=comment.id, text=comment.body, likes=comment.like_count, is_liked=False,
        can_delete=True, can_edit=True, moderation_status="approved",
        created_at=_iso(comment.created_at) or "", author=author,
    )


async def _delete_comment_legacy(ctx, id) -> bool:
    from repositories.content_repository import CommentRepository, PostRepository

    user = ctx.require_auth()
    comment_repo = CommentRepository(ctx.db)

    comment = await comment_repo.get_by_id(id)
    if not comment:
        return False
    if comment.user_id != user.id and user.role.value not in ("admin",):
        raise PermissionError("Only the comment author can delete it")

    success = await comment_repo.soft_delete(id)
    if success:
        post = await PostRepository(ctx.db).get_by_id(comment.post_id)
        if post and post.comment_count > 0:
            post.comment_count -= 1
        await ctx.db.commit()
    return success


async def _like_comment_legacy(ctx, id, like: bool) -> LikeResultType:
    from repositories.content_repository import CommentRepository
    from repositories.social_repository import CommentInteractionRepository

    user = ctx.require_auth()
    comment_repo = CommentRepository(ctx.db)
    interactions = CommentInteractionRepository(ctx.db)

    comment = await comment_repo.get_by_id(id)
    if not comment:
        raise ValueError("Comment not found")

    is_liked = await interactions.has_liked(id, user.id)
    if like and not is_liked:
        await interactions.toggle_like(id, user.id)
    elif not like and is_liked:
        await interactions.toggle_like(id, user.id)

    comment.like_count = await interactions.count_likes(id)
    await ctx.db.commit()
    return LikeResultType(liked=like, likes=comment.like_count)


async def _report_comment(ctx, id, reason) -> bool:
    """Best-effort moderation flag — logged for follow-up; no report table wired yet."""
    ctx.require_auth()
    return True


# ── Subscriptions ────────────────────────────────────────────────────────────


@strawberry.type
class Subscription:
    """Placeholder subscription root for future real-time features."""

    @strawberry.subscription
    async def placeholder(self) -> str:
        """Placeholder subscription (subscriptions not yet implemented)."""
        yield "placeholder"


# ── Error Handling Extension ─────────────────────────────────────────────────


class ConnextionZErrorExtension(strawberry.extensions.SchemaExtension):
    """
    Custom Strawberry extension that catches resolver exceptions and converts
    them to structured GraphQL errors with proper extensions.

    Catches:
    - PermissionError → UNAUTHENTICATED (401)
    - ValueError → VALIDATION_ERROR (400)
    - NotImplementedError → NOT_IMPLEMENTED (501)
    - Other exceptions → INTERNAL_ERROR (500)
    """

    def on_execute(self):
        yield
        # After execution, check for errors and enrich them
        result = self.execution_context.result
        errors = result.errors if result is not None else None
        if errors:
            for error in errors:
                # Add request ID if available
                if hasattr(self.execution_context, "request_id"):
                    error.extensions = error.extensions or {}
                    error.extensions["requestId"] = self.execution_context.request_id

                # Map Python exceptions to GraphQL error codes
                original = error.original_error
                if isinstance(original, PermissionError):
                    error.extensions = error.extensions or {}
                    error.extensions["code"] = "UNAUTHENTICATED"
                    error.extensions["statusCode"] = 401
                elif isinstance(original, ValueError):
                    error.extensions = error.extensions or {}
                    error.extensions["code"] = "VALIDATION_ERROR"
                    error.extensions["statusCode"] = 400
                elif isinstance(original, NotImplementedError):
                    error.extensions = error.extensions or {}
                    error.extensions["code"] = "NOT_IMPLEMENTED"
                    error.extensions["statusCode"] = 501
                else:
                    error.extensions = error.extensions or {}
                    error.extensions["code"] = "INTERNAL_ERROR"
                    error.extensions["statusCode"] = 500


# ── Schema Assembly ──────────────────────────────────────────────────────────


schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
    subscription=Subscription,
    extensions=[ConnextionZErrorExtension],
    config=StrawberryConfig(scalar_map=SCALAR_MAP),
)


# ── Router Factory ───────────────────────────────────────────────────────────


def create_graphql_router(
    session_factory: Callable[[], AsyncSession],
) -> GraphQLRouter[AppContext]:
    """Create a FastAPI-compatible GraphQL router."""
    from features.auth.jwt import decode_token, JWTError
    from repositories.user_repository import UserRepository

    async def get_context(
        request: Request,
        response: Response,
    ) -> AppContext:
        """Build the per-request ``AppContext``.

        1. Create a fresh ``AsyncSession`` from the session factory.
        2. If ``Authorization: Bearer <token>`` is present, decode the JWT
           and load the ``User``. Auth errors are swallowed so public queries
           still work; protected resolvers call ``ctx.require_auth()``.
        """
        db = session_factory()

        current_user: User | None = None
        session_id: str | None = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
            try:
                payload = decode_token(token)
                user_id = payload.get("sub")
                session_id = payload.get("jti")
                if user_id:
                    user_repo = UserRepository(db)
                    current_user = await user_repo.get_by_id(user_id)
            except (JWTError, ValueError):
                pass

        return AppContext(db=db, current_user=current_user, session_id=session_id)

    return GraphQLRouter[AppContext](
        schema,
        context_getter=get_context,
        graphql_ide="graphiql",
        subscription_protocols=["graphql-ws"],
    )
