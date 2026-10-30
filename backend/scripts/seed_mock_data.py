"""
Generate realistic mock data for ConnextionZ development and beta testing.

Synthetic users are identified by the reserved
@seed.connextionz.test domain.

Run from the repository root with:

    $env:PYTHONPATH = "$PWD\\backend"
    python .\\backend\\scripts\\seed_mock_data.py
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone

from faker import Faker
from sqlalchemy import func, select

from app.db.session import async_session_factory
from app.models.category import Category
from app.models.content import Comment, ContentStatus, ContentType, Post
from app.models.collaboration import (
    Collaboration,
    CollaborationParticipant,
    CollaborationStatus,
    Milestone,
    MilestoneStatus,
)
from app.models.messaging import (
    Conversation,
    ConversationParticipant,
    Message,
)
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationType,
)
from app.models.reputation import (
    Badge,
    Endorsement,
    ReputationScore,
    UserBadge,
)
from app.models.social import (
    CommentLike,
    Follow,
    PostLike,
    PostSave,
    PostShare,
    PostWatch,
)
from app.models.user import (
    AccountStatus,
    Profile,
    User,
    UserRole,
)
from app.models.analytics import (
    AnalyticsEvent,
    EventType,
    InteractionSignal,
    SignalType,
)
from features.auth.password import hash_password
from services.analytics_event_service import AnalyticsEventService
# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FAKER_SEED = 20261027
USER_COUNT = 25
POST_COUNT = 100
COMMENT_COUNT = 250
FOLLOW_COUNT = 150
POST_LIKE_COUNT = 500
POST_SAVE_COUNT = 100
POST_SHARE_COUNT = 50
POST_WATCH_COUNT = 800
COMMENT_LIKE_COUNT = 300
COLLABORATION_COUNT = 20
MILESTONE_COUNT = 40
CONVERSATION_COUNT = 15
MESSAGE_COUNT = 150
NOTIFICATION_COUNT = 200
ENDORSEMENT_COUNT = 40
PROFILE_VIEW_EVENT_COUNT = 150
SEARCH_EVENT_COUNT = 75
NOT_INTERESTED_COUNT = 30
FAKE_EMAIL_DOMAIN = "seed.connextionz.test"
FAKE_PASSWORD = "ConnextionZ_Test_2026!"

fake = Faker("en_US")

Faker.seed(FAKER_SEED)
random.seed(FAKER_SEED)


# ---------------------------------------------------------------------------
# Fake profile data
# ---------------------------------------------------------------------------

CREATOR_TAGS = [
    "art",
    "music",
    "gaming",
    "photography",
    "fashion",
    "technology",
    "writing",
    "design",
    "video",
    "fitness",
    "food",
    "travel",
    "crafts",
    "education",
    "comedy",
]

COLLAB_TYPES = [
    "content creation",
    "video",
    "livestream",
    "podcast",
    "music",
    "photography",
    "design",
    "social campaign",
]

RESPONSE_TIMES = [
    "< 1 hour",
    "< 4 hours",
    "< 12 hours",
    "< 24 hours",
    "1-2 days",
]

COLLAB_STATUSES = [
    "Open to collaborations",
    "Looking for creators",
    "Available for projects",
    "Selective collaborations",
    None,
]

POST_TOPICS = {
    "art": [
        "Working on a new piece today and experimenting with a different style.",
        "Finally finished this project. I learned a lot while making it.",
        "A little look at what I've been creating lately.",
    ],
    "business": [
        "Spent today working through some new ideas for an upcoming project.",
        "Small improvements add up. Today's focus was refining the process.",
        "Trying a new approach to planning my next project.",
    ],
    "cooking": [
        "Tried something new in the kitchen today and it turned out better than expected.",
        "Today's experiment was definitely worth saving.",
        "Working on a new recipe and already thinking about what I'd change next time.",
    ],
    "fashion": [
        "Putting together some ideas for a new look.",
        "Today's inspiration came from playing with different textures and details.",
        "Trying something a little different with this one.",
    ],
    "fitness": [
        "Today's session was all about consistency.",
        "Progress doesn't always look dramatic, but it still counts.",
        "Trying a different routine today and seeing how it feels.",
    ],
    "gaming": [
        "Had one of those gaming sessions where everything somehow worked.",
        "Trying something different with my setup today.",
        "This was definitely not how I expected that match to go.",
    ],
    "music": [
        "Working on a new idea and finally starting to like where it's going.",
        "Spent some time experimenting with a different sound today.",
        "Sometimes one small change completely transforms a track.",
    ],
    "tech": [
        "Testing a new idea today and learning a few things along the way.",
        "Finally got this working after more troubleshooting than I'd like to admit.",
        "Experimenting with a different approach to this project.",
    ],
    "travel": [
        "Found a place today that I definitely want to visit again.",
        "One of my favorite parts of traveling is finding places I wasn't looking for.",
        "Saving this moment from today's adventure.",
    ],
}

COMMENT_TEXTS = [
    "This turned out really well!",
    "I love this idea.",
    "Definitely interested in seeing more of this.",
    "This is such a good approach.",
    "I've been thinking about trying something similar.",
    "This came together really nicely.",
    "Thanks for sharing this!",
    "I needed to see this today.",
    "This is really inspiring.",
    "That looks great!",
    "I'd love to hear more about this.",
    "How long did this take?",
    "What inspired you to try this?",
    "This would be fun to collaborate on.",
    "I really like the direction you're taking with this.",
    "Saving this idea for later.",
    "This is exactly the kind of content I enjoy seeing here.",
    "Nice work!",
    "This is really creative.",
    "I'd definitely try this.",
]

REPLY_TEXTS = [
    "Same here!",
    "I was thinking the same thing.",
    "That's a good point.",
    "Absolutely!",
    "I agree.",
    "I'd like to know that too.",
    "That would be really interesting.",
    "Definitely.",
    "Good question!",
    "I hadn't thought about that.",
]

COLLABORATION_IDEAS = [
    (
        "Creator Spotlight Video",
        "Create a collaborative spotlight featuring both creators and their work.",
        "video",
        "youtube",
    ),
    (
        "Community Livestream",
        "Host a collaborative livestream and interact with both communities.",
        "livestream",
        "twitch",
    ),
    (
        "Creative Challenge",
        "Work together on a themed creative challenge and share the results.",
        "post",
        "instagram",
    ),
    (
        "Gaming Collaboration",
        "Team up for a gaming session and create highlights from the collaboration.",
        "video",
        "youtube",
    ),
    (
        "Tech Project Showcase",
        "Build and showcase a small project together for the community.",
        "video",
        "youtube",
    ),
    (
        "Creator Interview",
        "Record a conversational interview about creative work and experiences.",
        "podcast",
        "spotify",
    ),
    (
        "Collaborative Tutorial",
        "Create an educational tutorial combining both creators' skills.",
        "video",
        "youtube",
    ),
    (
        "Community Q&A",
        "Host a collaborative question and answer session for the community.",
        "livestream",
        "youtube",
    ),
]

MILESTONE_IDEAS = [
    (
        "Plan collaboration",
        "Define the collaboration goals, responsibilities, and creative direction.",
    ),
    (
        "Prepare content",
        "Create and prepare the content needed for the collaboration.",
    ),
    (
        "Review draft",
        "Review the current draft and provide feedback before publishing.",
    ),
    (
        "Record content",
        "Record the planned collaborative content.",
    ),
    (
        "Edit content",
        "Complete editing and prepare the final version.",
    ),
    (
        "Final approval",
        "Review and approve the finished collaboration content.",
    ),
    (
        "Publish collaboration",
        "Publish the completed collaboration to the selected platform.",
    ),
    (
        "Promote collaboration",
        "Share and promote the collaboration with both communities.",
    ),
]

MESSAGE_TEXTS = [
    "Hey! I saw your profile and really like what you're working on.",
    "Would you be interested in collaborating on something together?",
    "That sounds good to me. What did you have in mind?",
    "I think our audiences would really enjoy this.",
    "I'm definitely interested. Let's figure out the details.",
    "What kind of timeline were you thinking?",
    "I should have some time later this week.",
    "That works for me.",
    "I'll put together a few ideas and send them over.",
    "I really like that direction.",
    "Could we make the concept a little more casual?",
    "Absolutely. I think that would work better too.",
    "Do you want to handle the first draft?",
    "Sure, I can get started on it.",
    "I'll review it once you send it over.",
    "Thanks! I'll keep you updated.",
    "I finished the first part. Let me know what you think.",
    "This looks great so far.",
    "I have a couple small changes, but we're really close.",
    "Sounds good. Send them whenever you're ready.",
    "Are we still good for this weekend?",
    "Yep! Everything is still on schedule.",
    "Perfect. I'm looking forward to it.",
    "Thanks for working on this with me!",
]
ENDORSEMENT_COMMENTS = [
    "Great communication throughout the collaboration.",
    "Really easy to work with and very reliable.",
    "Brought a lot of creative ideas to the project.",
    "Delivered everything we agreed on.",
    "Would definitely collaborate again.",
    "Very responsive and easy to coordinate with.",
    "Strong creative direction and attention to detail.",
    "Made the entire collaboration process smooth.",
    "Reliable, professional, and collaborative.",
    "Great partner from planning through completion.",
]

BADGE_DEFINITIONS = [
    {
        "name": "First Collaboration",
        "description": "Participated in a collaboration.",
        "category": "collaboration",
        "tier": 1,
        "criteria": {"min_collaborations": 1},
    },
    {
        "name": "Active Collaborator",
        "description": "Participated in multiple collaborations.",
        "category": "collaboration",
        "tier": 2,
        "criteria": {"min_collaborations": 2},
    },
    {
        "name": "Community Builder",
        "description": "Built connections with other creators.",
        "category": "community",
        "tier": 1,
        "criteria": {"min_followers": 5},
    },
    {
        "name": "Content Creator",
        "description": "Published multiple pieces of content.",
        "category": "content",
        "tier": 1,
        "criteria": {"min_posts": 3},
    },
    {
        "name": "Audience Favorite",
        "description": "Received strong engagement on content.",
        "category": "content",
        "tier": 2,
        "criteria": {"min_likes": 15},
    },
    {
        "name": "Milestone Maker",
        "description": "Completed a collaboration milestone.",
        "category": "milestone",
        "tier": 1,
        "criteria": {"completed_milestones": 1},
    },
]
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_username(index: int, first_name: str, last_name: str) -> str:
    """Create a deterministic unique username."""

    first = "".join(c for c in first_name.lower() if c.isalnum())
    last = "".join(c for c in last_name.lower() if c.isalnum())

    return f"{first}.{last}.{index:03d}"[:64]


def make_bio() -> str:
    """Generate a short creator-style profile biography."""

    templates = [
        "{job} creating, connecting, and trying new ideas.",
        "{job} sharing projects, inspiration, and creative work.",
        "Independent {job} interested in collaboration and new projects.",
        "{job} exploring creativity, community, and digital media.",
        "Creating things, meeting people, and working on what's next.",
    ]

    return random.choice(templates).format(
        job=fake.job().lower()
    )


async def show_existing_data() -> tuple[int, int]:
    """Display existing database and seed-user counts."""

    async with async_session_factory() as session:
        total_users = await session.scalar(
            select(func.count()).select_from(User)
        )

        fake_users = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
        )

    total_users = total_users or 0
    fake_users = fake_users or 0

    print()
    print("ConnextionZ seed status")
    print("------------------------")
    print(f"Existing users: {total_users}")
    print(f"Existing Faker users: {fake_users}")
    print(f"Planned Faker users: {USER_COUNT}")

    return total_users, fake_users


# ---------------------------------------------------------------------------
# User + Profile seeding
# ---------------------------------------------------------------------------

async def seed_users_and_profiles() -> None:
    """Create Faker users and their corresponding profiles."""

    async with async_session_factory() as session:

        existing = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
        )

        if existing:
            print()
            print(
                f"Seed users already exist ({existing}). "
                "No users were created."
            )
            return

        print()
        print(f"Creating {USER_COUNT} Faker users and profiles...")

        # Hash once because every generated development account
        # intentionally uses the same test password.
        password_hash = hash_password(FAKE_PASSWORD)

        try:
            for index in range(1, USER_COUNT + 1):

                first_name = fake.first_name()
                last_name = fake.last_name()

                username = make_username(
                    index,
                    first_name,
                    last_name,
                )

                email = (
                    f"faker_{index:03d}@{FAKE_EMAIL_DOMAIN}"
                )

                role = random.choices(
                    [UserRole.CREATOR, UserRole.USER],
                    weights=[75, 25],
                    k=1,
                )[0]

                user = User(
                    email=email,
                    username=username,
                    hashed_password=password_hash,
                    role=role,
                    status=AccountStatus.ACTIVE,
                    email_verified=True,
                    mfa_enabled=False,
                )

                session.add(user)

                # Generate the UUID before creating Profile.user_id.
                await session.flush()

                tags = random.sample(
                    CREATOR_TAGS,
                    k=random.randint(2, 5),
                )

                collab_types = random.sample(
                    COLLAB_TYPES,
                    k=random.randint(1, 3),
                )

                profile = Profile(
                    user_id=user.id,
                    display_name=f"{first_name} {last_name}",
                    bio=make_bio(),
                    location=f"{fake.city()}, {fake.state_abbr()}",
                    website_url=(
                        fake.url()
                        if random.random() < 0.45
                        else None
                    ),
                    social_links={
                        "instagram": f"@{username.replace('.', '')}",
                    },
                    tags=tags,
                    follower_count=0,
                    following_count=0,
                    collaboration_count=0,
                    total_likes=0,
                    avatar_color=random.choice(
                        [
                            "#7C5CFC",
                            "#E76F8A",
                            "#4F9D8A",
                            "#D28C45",
                            "#5378C7",
                            "#9B6BC2",
                        ]
                    ),
                    verified=random.random() < 0.08,
                    online=random.random() < 0.35,
                    collab_status=random.choice(COLLAB_STATUSES),
                    collab_score=round(
                        random.uniform(20.0, 95.0),
                        1,
                    ),
                    open_to_collab=random.random() < 0.80,
                    private_account=random.random() < 0.10,
                    response_time=random.choice(RESPONSE_TIMES),
                    onboarding_collab_types=collab_types,
                )

                session.add(profile)

            await session.commit()

        except Exception:
            await session.rollback()
            raise

    print(f"Created {USER_COUNT} users.")
    print(f"Created {USER_COUNT} profiles.")

async def assign_profile_categories() -> None:
    """Assign existing canonical categories to Faker-generated profiles."""

    async with async_session_factory() as session:
        # Load only profiles belonging to our generated seed users.
        result = await session.execute(
            select(Profile)
            .join(User, Profile.user_id == User.id)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )

        profiles = result.scalars().unique().all()

        # Load ConnextionZ's existing canonical categories.
        result = await session.execute(
            select(Category).order_by(Category.name)
        )

        categories = result.scalars().all()

        if not profiles:
            print("No Faker profiles found.")
            return

        if not categories:
            print("No categories found.")
            return

        assigned_profiles = 0
        total_assignments = 0

        for profile in profiles:
            # Safe to rerun: don't replace an existing assignment.
            if profile.categories:
                continue

            number_to_assign = random.randint(2, min(4, len(categories)))

            selected_categories = random.sample(
                categories,
                k=number_to_assign,
            )

            profile.categories.extend(selected_categories)

            assigned_profiles += 1
            total_assignments += len(selected_categories)

        await session.commit()

        print()
        print("Profile categories")
        print("------------------")
        print(f"Profiles updated: {assigned_profiles}")
        print(f"Category assignments created: {total_assignments}")

async def seed_posts() -> None:
    """Create realistic posts for Faker-generated users."""

    async with async_session_factory() as session:
        existing_posts = await session.scalar(
            select(func.count())
            .select_from(Post)
            .join(User, Post.user_id == User.id)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
        )

        if existing_posts:
            print()
            print("Posts")
            print("-----")
            print(f"Existing Faker posts: {existing_posts}")
            print("Skipping post creation.")
            return

        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        result = await session.execute(
            select(Profile)
            .join(User, Profile.user_id == User.id)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        profiles = result.scalars().unique().all()

        if not users or not profiles:
            print("No Faker users/profiles available for posts.")
            return

        profiles_by_user = {
            profile.user_id: profile
            for profile in profiles
        }

        creator_weights = [
            random.uniform(0.5, 3.0)
            for _ in users
        ]

        now = datetime.now(timezone.utc)

        created = 0
        type_counts = {
            content_type: 0
            for content_type in ContentType
        }

        try:
            for _ in range(POST_COUNT):
                user = random.choices(
                    users,
                    weights=creator_weights,
                    k=1,
                )[0]

                profile = profiles_by_user[user.id]

                category_slugs = [
                    category.slug
                    for category in profile.categories
                ]

                if category_slugs:
                    topic = random.choice(category_slugs)
                else:
                    topic = random.choice(list(POST_TOPICS))

                caption = random.choice(
                    POST_TOPICS.get(
                        topic,
                        POST_TOPICS["art"],
                    )
                )

                content_type = random.choices(
                    [
                        ContentType.POST,
                        ContentType.IMAGE,
                        ContentType.VIDEO,
                        ContentType.AUDIO,
                    ],
                    weights=[35, 35, 25, 5],
                    k=1,
                )[0]

                status = random.choices(
                    [
                        ContentStatus.PUBLISHED,
                        ContentStatus.DRAFT,
                    ],
                    weights=[94, 6],
                    k=1,
                )[0]

                age = timedelta(
                    days=random.randint(0, 60),
                    hours=random.randint(0, 23),
                    minutes=random.randint(0, 59),
                )

                post_time = now - age

                hashtags = [f"#{topic}"]

                if random.random() < 0.60:
                    hashtags.append("#creator")

                if random.random() < 0.35:
                    hashtags.append("#collaboration")

                published_at = (
                    post_time.isoformat()
                    if status == ContentStatus.PUBLISHED
                    else None
                )

                post = Post(
                    user_id=user.id,
                    content_type=content_type,
                    status=status,
                    title=(
                        fake.sentence(nb_words=5).rstrip(".")
                        if content_type == ContentType.POST
                        and random.random() < 0.35
                        else None
                    ),
                    body=(
                        fake.paragraph(nb_sentences=2)
                        if content_type == ContentType.POST
                        and random.random() < 0.45
                        else None
                    ),
                    caption=caption,
                    tags=[topic],
                    mentions=[],
                    like_count=0,
                    comment_count=0,
                    share_count=0,
                    view_count=0,
                    save_count=0,
                    published_at=published_at,
                    hashtags=hashtags,
                    audio="Original Sound",
                    visibility="public",
                    allow_comments=random.random() < 0.95,
                    allow_collabs=random.random() < 0.80,
                    duration_sec=(
                        round(random.uniform(8, 180), 1)
                        if content_type in (
                            ContentType.VIDEO,
                            ContentType.AUDIO,
                        )
                        else 0.0
                    ),
                    moderation_status="approved",
                )

                session.add(post)

                created += 1
                type_counts[content_type] += 1

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        print()
        print("Posts")
        print("-----")
        print(f"Posts created: {created}")

        for content_type, count in type_counts.items():
            if count:
                print(f"  {content_type.value}: {count}")      

async def seed_comments() -> None:
    """Create comments and replies on Faker-generated posts."""

    async with async_session_factory() as session:
        existing_comments = await session.scalar(
            select(func.count())
            .select_from(Comment)
            .join(Post, Comment.post_id == Post.id)
            .join(User, Post.user_id == User.id)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
        )

        if existing_comments:
            print()
            print("Comments")
            print("--------")
            print(f"Existing Faker-post comments: {existing_comments}")
            print("Skipping comment creation.")
            return

        # Load Faker users who can write comments.
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )

        users = result.scalars().all()

        # Only comment on published Faker posts that allow comments.
        result = await session.execute(
            select(Post)
            .join(User, Post.user_id == User.id)
            .where(
                User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"),
                Post.status == ContentStatus.PUBLISHED,
                Post.allow_comments.is_(True),
            )
        )

        posts = result.scalars().unique().all()

        if not users:
            print("No Faker users available for comments.")
            return

        if not posts:
            print("No eligible Faker posts available for comments.")
            return

        created_comments: list[Comment] = []

        try:
            # First create regular top-level comments.
            #
            # About 80% of the target will be top-level comments.
            top_level_target = int(COMMENT_COUNT * 0.80)

            for _ in range(top_level_target):
                post = random.choice(posts)

                # Prefer somebody other than the post owner.
                possible_commenters = [
                    user
                    for user in users
                    if user.id != post.user_id
                ]

                commenter = random.choice(
                    possible_commenters or users
                )

                comment = Comment(
                    post_id=post.id,
                    user_id=commenter.id,
                    parent_id=None,
                    body=random.choice(COMMENT_TEXTS),
                    is_edited=random.random() < 0.03,
                    like_count=0,
                    moderation_status="approved",
                )

                session.add(comment)
                created_comments.append(comment)

            # Flush so top-level comments receive their UUIDs.
            await session.flush()

            # Remaining ~20% become replies.
            reply_target = COMMENT_COUNT - top_level_target

            for _ in range(reply_target):
                parent = random.choice(created_comments)

                possible_repliers = [
                    user
                    for user in users
                    if user.id != parent.user_id
                ]

                replier = random.choice(
                    possible_repliers or users
                )

                reply = Comment(
                    post_id=parent.post_id,
                    user_id=replier.id,
                    parent_id=parent.id,
                    body=random.choice(REPLY_TEXTS),
                    is_edited=random.random() < 0.02,
                    like_count=0,
                    moderation_status="approved",
                )

                session.add(reply)

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        print()
        print("Comments")
        print("--------")
        print(f"Comments created: {COMMENT_COUNT}")
        print(f"  Top-level: {top_level_target}")
        print(f"  Replies: {reply_target}")
        print()

async def seed_follows() -> None:
    """Create unique follow relationships between Faker users."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        if len(users) < 2:
            print("Not enough Faker users to create follows.")
            return

        user_ids = [user.id for user in users]
        user_id_set = set(user_ids)

        # Find any follow relationships that already exist entirely
        # inside the Faker user group.
        result = await session.execute(
            select(Follow.follower_id, Follow.following_id)
            .where(
                Follow.follower_id.in_(user_ids),
                Follow.following_id.in_(user_ids),
            )
        )

        existing_pairs = set(result.all())

        print()
        print("Follows")
        print("-------")

        if len(existing_pairs) >= FOLLOW_COUNT:
            print(f"Existing Faker follows: {len(existing_pairs)}")
            print("Skipping follow creation.")

        else:
            needed = FOLLOW_COUNT - len(existing_pairs)

            # Build every valid directional pair.
            possible_pairs = [
                (follower_id, following_id)
                for follower_id in user_ids
                for following_id in user_ids
                if follower_id != following_id
                and (follower_id, following_id) not in existing_pairs
            ]

            random.shuffle(possible_pairs)

            selected_pairs = possible_pairs[:needed]

            try:
                for follower_id, following_id in selected_pairs:
                    session.add(
                        Follow(
                            follower_id=follower_id,
                            following_id=following_id,
                        )
                    )

                await session.commit()

            except Exception:
                await session.rollback()
                raise

            print(f"Existing Faker follows: {len(existing_pairs)}")
            print(f"Follows created: {len(selected_pairs)}")

        # Recalculate profile counters from actual Follow rows.
        result = await session.execute(
            select(Profile)
            .where(Profile.user_id.in_(user_ids))
        )
        profiles = result.scalars().all()

        for profile in profiles:
            follower_count = await session.scalar(
                select(func.count())
                .select_from(Follow)
                .where(
                    Follow.following_id == profile.user_id,
                    Follow.follower_id.in_(user_id_set),
                )
            )

            following_count = await session.scalar(
                select(func.count())
                .select_from(Follow)
                .where(
                    Follow.follower_id == profile.user_id,
                    Follow.following_id.in_(user_id_set),
                )
            )

            profile.follower_count = follower_count or 0
            profile.following_count = following_count or 0

        try:
            await session.commit()

        except Exception:
            await session.rollback()
            raise

        total_follows = await session.scalar(
            select(func.count())
            .select_from(Follow)
            .where(
                Follow.follower_id.in_(user_id_set),
                Follow.following_id.in_(user_id_set),
            )
        )

        print(f"Total Faker follows: {total_follows}")
        print("Profile follow counters updated.")

async def seed_post_engagement() -> None:
    """Create realistic engagement for Faker-generated posts."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        result = await session.execute(
            select(Post)
            .join(User, Post.user_id == User.id)
            .where(
                User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"),
                Post.status == ContentStatus.PUBLISHED,
            )
        )
        posts = result.scalars().unique().all()

        if not users or not posts:
            print("No Faker users/posts available for engagement.")
            return

        user_ids = [user.id for user in users]
        post_ids = [post.id for post in posts]

        print()
        print("Post engagement")
        print("---------------")

        # Give some posts substantially more engagement than others.
        post_weights = [
            random.uniform(0.25, 4.0)
            for _ in posts
        ]

        async def seed_unique_engagement(
            model,
            target_count: int,
            label: str,
        ) -> None:
            result = await session.execute(
                select(model.post_id, model.user_id)
                .where(
                    model.post_id.in_(post_ids),
                    model.user_id.in_(user_ids),
                )
            )

            existing_pairs = set(result.all())

            if len(existing_pairs) >= target_count:
                print(
                    f"{label}: {len(existing_pairs)} existing, "
                    "creation skipped."
                )
                return

            needed = target_count - len(existing_pairs)

            available_pairs = [
                (post.id, user.id)
                for post in posts
                for user in users
                if user.id != post.user_id
                and (post.id, user.id) not in existing_pairs
            ]

            # Weight pairs by post popularity so engagement is not
            # distributed perfectly evenly across every post.
            weight_by_post = {
                post.id: weight
                for post, weight in zip(posts, post_weights)
            }

            selected_pairs = []

            while available_pairs and len(selected_pairs) < needed:
                pair_weights = [
                    weight_by_post[post_id]
                    for post_id, _ in available_pairs
                ]

                selected = random.choices(
                    available_pairs,
                    weights=pair_weights,
                    k=1,
                )[0]

                selected_pairs.append(selected)
                available_pairs.remove(selected)

            for post_id, user_id in selected_pairs:
                session.add(
                    model(
                        post_id=post_id,
                        user_id=user_id,
                    )
                )

            await session.flush()

            print(
                f"{label}: {len(existing_pairs)} existing, "
                f"{len(selected_pairs)} created."
            )

        try:
            await seed_unique_engagement(
                PostLike,
                POST_LIKE_COUNT,
                "Post likes",
            )

            await seed_unique_engagement(
                PostSave,
                POST_SAVE_COUNT,
                "Post saves",
            )

            await seed_unique_engagement(
                PostShare,
                POST_SHARE_COUNT,
                "Post shares",
            )

            # Watches are events, not unique user/post relationships.
            existing_watches = await session.scalar(
                select(func.count())
                .select_from(PostWatch)
                .where(
                    PostWatch.post_id.in_(post_ids),
                    PostWatch.user_id.in_(user_ids),
                )
            )

            existing_watches = existing_watches or 0

            watches_needed = max(
                0,
                POST_WATCH_COUNT - existing_watches,
            )

            if watches_needed:
                watch_history: set[tuple] = set()

                for _ in range(watches_needed):
                    post = random.choices(
                        posts,
                        weights=post_weights,
                        k=1,
                    )[0]

                    possible_watchers = [
                        user
                        for user in users
                        if user.id != post.user_id
                    ]

                    watcher = random.choice(
                        possible_watchers or users
                    )

                    watch_key = (post.id, watcher.id)

                    is_rewatch = (
                        watch_key in watch_history
                        or random.random() < 0.12
                    )

                    watch_history.add(watch_key)

                    if post.duration_sec > 0:
                        completed = random.random() < 0.58

                        if completed:
                            watched_seconds = post.duration_sec
                        else:
                            watched_seconds = round(
                                random.uniform(
                                    1.0,
                                    max(1.0, post.duration_sec * 0.95),
                                ),
                                1,
                            )
                    else:
                        completed = random.random() < 0.70
                        watched_seconds = round(
                            random.uniform(1.0, 20.0),
                            1,
                        )

                    session.add(
                        PostWatch(
                            post_id=post.id,
                            user_id=watcher.id,
                            watched_seconds=watched_seconds,
                            completed=completed,
                            rewatched=is_rewatch,
                        )
                    )

                await session.flush()

            print(
                f"Post watches: {existing_watches} existing, "
                f"{watches_needed} created."
            )

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        # Recalculate post counters from the actual relationship rows.
        result = await session.execute(
            select(Post)
            .where(Post.id.in_(post_ids))
        )
        posts_to_update = result.scalars().all()

        for post in posts_to_update:
            post.like_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(PostLike)
                    .where(PostLike.post_id == post.id)
                )
                or 0
            )

            post.save_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(PostSave)
                    .where(PostSave.post_id == post.id)
                )
                or 0
            )

            post.share_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(PostShare)
                    .where(PostShare.post_id == post.id)
                )
                or 0
            )

            post.comment_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(Comment)
                    .where(Comment.post_id == post.id)
                )
                or 0
            )

            post.view_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(PostWatch)
                    .where(PostWatch.post_id == post.id)
                )
                or 0
            )

        await session.commit()

        print("Post counters recalculated from engagement rows.")

async def seed_comment_likes() -> None:
    """Create likes on Faker-generated comments."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        result = await session.execute(
            select(Comment)
            .join(Post, Comment.post_id == Post.id)
            .join(User, Post.user_id == User.id)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
        )
        comments = result.scalars().unique().all()

        if not users or not comments:
            print("No Faker users/comments available for comment likes.")
            return

        user_ids = [user.id for user in users]
        comment_ids = [comment.id for comment in comments]

        result = await session.execute(
            select(CommentLike.comment_id, CommentLike.user_id)
            .where(
                CommentLike.comment_id.in_(comment_ids),
                CommentLike.user_id.in_(user_ids),
            )
        )

        existing_pairs = set(result.all())

        print()
        print("Comment likes")
        print("-------------")

        if len(existing_pairs) >= COMMENT_LIKE_COUNT:
            print(f"Existing Faker comment likes: {len(existing_pairs)}")
            print("Skipping comment-like creation.")

        else:
            needed = COMMENT_LIKE_COUNT - len(existing_pairs)

            # Some comments should naturally attract more likes than others.
            comment_weights = {
                comment.id: random.uniform(0.2, 4.0)
                for comment in comments
            }

            available_pairs = [
                (comment.id, user.id)
                for comment in comments
                for user in users
                if user.id != comment.user_id
                and (comment.id, user.id) not in existing_pairs
            ]

            selected_pairs = []

            while available_pairs and len(selected_pairs) < needed:
                pair_weights = [
                    comment_weights[comment_id]
                    for comment_id, _ in available_pairs
                ]

                selected = random.choices(
                    available_pairs,
                    weights=pair_weights,
                    k=1,
                )[0]

                selected_pairs.append(selected)
                available_pairs.remove(selected)

            try:
                for comment_id, user_id in selected_pairs:
                    session.add(
                        CommentLike(
                            comment_id=comment_id,
                            user_id=user_id,
                        )
                    )

                await session.commit()

            except Exception:
                await session.rollback()
                raise

            print(f"Existing Faker comment likes: {len(existing_pairs)}")
            print(f"Comment likes created: {len(selected_pairs)}")

        # Recalculate like_count from the actual CommentLike rows.
        result = await session.execute(
            select(Comment)
            .where(Comment.id.in_(comment_ids))
        )
        comments_to_update = result.scalars().all()

        for comment in comments_to_update:
            comment.like_count = (
                await session.scalar(
                    select(func.count())
                    .select_from(CommentLike)
                    .where(CommentLike.comment_id == comment.id)
                )
                or 0
            )

        try:
            await session.commit()

        except Exception:
            await session.rollback()
            raise

        total_comment_likes = await session.scalar(
            select(func.count())
            .select_from(CommentLike)
            .where(
                CommentLike.comment_id.in_(comment_ids),
                CommentLike.user_id.in_(user_ids),
            )
        )

        print(f"Total Faker comment likes: {total_comment_likes}")
        print("Comment like counters updated.")
async def seed_collaborations() -> None:
    """Create realistic collaborations between Faker users."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        if len(users) < 2:
            print("Not enough Faker users for collaborations.")
            return

        user_ids = [user.id for user in users]

        existing_count = await session.scalar(
            select(func.count())
            .select_from(Collaboration)
            .where(Collaboration.initiator_id.in_(user_ids))
        )

        existing_count = existing_count or 0

        print()
        print("Collaborations")
        print("--------------")

        if existing_count >= COLLABORATION_COUNT:
            print(f"Existing Faker collaborations: {existing_count}")
            print("Skipping collaboration creation.")
            return

        needed = COLLABORATION_COUNT - existing_count

        statuses = [
            CollaborationStatus.PROPOSED,
            CollaborationStatus.ACCEPTED,
            CollaborationStatus.IN_PROGRESS,
            CollaborationStatus.COMPLETED,
            CollaborationStatus.DECLINED,
            CollaborationStatus.CANCELLED,
        ]

        status_weights = [20, 15, 25, 25, 8, 7]

        now = datetime.now(timezone.utc)

        created = 0
        status_counts = {
            status: 0
            for status in CollaborationStatus
        }

        try:
            for _ in range(needed):
                initiator = random.choice(users)

                possible_partners = [
                    user
                    for user in users
                    if user.id != initiator.id
                ]

                partner = random.choice(possible_partners)

                title, description, content_type, platform = random.choice(
                    COLLABORATION_IDEAS
                )

                status = random.choices(
                    statuses,
                    weights=status_weights,
                    k=1,
                )[0]

                proposed_time = now - timedelta(
                    days=random.randint(5, 90),
                    hours=random.randint(0, 23),
                )

                started_at = None
                completed_at = None
                started_time: datetime | None = None

                if status in (
                    CollaborationStatus.IN_PROGRESS,
                    CollaborationStatus.COMPLETED,
                ):
                    started_time = proposed_time + timedelta(
                        days=random.randint(1, 10)
                    )
                    started_at = started_time.isoformat()

                if (
                    status == CollaborationStatus.COMPLETED
                    and started_time is not None
                ):
                    completed_time = started_time + timedelta(
                        days=random.randint(2, 30)
                    )

                    if completed_time > now:
                        completed_time = now

                    completed_at = completed_time.isoformat()

                has_budget = random.random() < 0.45

                if has_budget:
                    budget_min = float(
                        random.choice([50, 100, 150, 200, 250, 500])
                    )

                    budget_max = budget_min + float(
                        random.choice([50, 100, 250, 500])
                    )
                else:
                    budget_min = None
                    budget_max = None

                collaboration = Collaboration(
                    initiator_id=initiator.id,
                    title=title,
                    description=description,
                    status=status,
                    content_type=content_type,
                    platform=platform,
                    tags=[
                        content_type,
                        "collaboration",
                    ],
                    proposed_at=proposed_time.isoformat(),
                    started_at=started_at,
                    completed_at=completed_at,
                    budget_min=budget_min,
                    budget_max=budget_max,
                    budget_currency="USD",
                )

                session.add(collaboration)
                await session.flush()

                initiator_accepted = status not in (
                    CollaborationStatus.DECLINED,
                    CollaborationStatus.CANCELLED,
                )

                partner_accepted = status in (
                    CollaborationStatus.ACCEPTED,
                    CollaborationStatus.IN_PROGRESS,
                    CollaborationStatus.COMPLETED,
                )

                session.add(
                    CollaborationParticipant(
                        collaboration_id=collaboration.id,
                        user_id=initiator.id,
                        role="initiator",
                        accepted=initiator_accepted,
                        accepted_at=(
                            proposed_time.isoformat()
                            if initiator_accepted
                            else None
                        ),
                    )
                )

                session.add(
                    CollaborationParticipant(
                        collaboration_id=collaboration.id,
                        user_id=partner.id,
                        role="participant",
                        accepted=partner_accepted,
                        accepted_at=(
                            (
                                proposed_time
                                + timedelta(days=random.randint(1, 5))
                            ).isoformat()
                            if partner_accepted
                            else None
                        ),
                    )
                )

                created += 1
                status_counts[status] += 1

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        print(f"Existing Faker collaborations: {existing_count}")
        print(f"Collaborations created: {created}")

        for status, count in status_counts.items():
            if count:
                print(f"  {status.value}: {count}")

        print(f"Participants created: {created * 2}")

async def seed_milestones() -> None:
    """Create realistic milestones for Faker collaborations."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User.id)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
        )
        fake_user_ids = list(result.scalars().all())

        if not fake_user_ids:
            print("No Faker users available for milestones.")
            return

        result = await session.execute(
            select(Collaboration)
            .where(Collaboration.initiator_id.in_(fake_user_ids))
        )
        collaborations = result.scalars().all()

        eligible_collaborations = [
            collaboration
            for collaboration in collaborations
            if collaboration.status
            not in (
                CollaborationStatus.DECLINED,
                CollaborationStatus.CANCELLED,
            )
        ]

        if not eligible_collaborations:
            print("No eligible Faker collaborations for milestones.")
            return

        collaboration_ids = [
            collaboration.id
            for collaboration in eligible_collaborations
        ]

        existing_count = await session.scalar(
            select(func.count())
            .select_from(Milestone)
            .where(
                Milestone.collaboration_id.in_(collaboration_ids)
            )
        )

        existing_count = existing_count or 0

        print()
        print("Collaboration milestones")
        print("------------------------")

        if existing_count >= MILESTONE_COUNT:
            print(f"Existing Faker milestones: {existing_count}")
            print("Skipping milestone creation.")
            return

        needed = MILESTONE_COUNT - existing_count
        now = datetime.now(timezone.utc)

        created = 0
        status_counts = {
            status: 0
            for status in MilestoneStatus
        }

        try:
            while created < needed:
                collaboration = random.choice(
                    eligible_collaborations
                )

                existing_for_collaboration = await session.scalar(
                    select(func.count())
                    .select_from(Milestone)
                    .where(
                        Milestone.collaboration_id
                        == collaboration.id
                    )
                )

                existing_for_collaboration = (
                    existing_for_collaboration or 0
                )

                # Keep individual collaborations from accumulating
                # an unrealistic number of milestones.
                if existing_for_collaboration >= 4:
                    continue

                title, description = random.choice(
                    MILESTONE_IDEAS
                )

                if (
                    collaboration.status
                    == CollaborationStatus.PROPOSED
                ):
                    milestone_status = MilestoneStatus.PENDING

                elif (
                    collaboration.status
                    == CollaborationStatus.ACCEPTED
                ):
                    milestone_status = random.choices(
                        [
                            MilestoneStatus.PENDING,
                            MilestoneStatus.IN_PROGRESS,
                        ],
                        weights=[80, 20],
                        k=1,
                    )[0]

                elif (
                    collaboration.status
                    == CollaborationStatus.IN_PROGRESS
                ):
                    milestone_status = random.choices(
                        [
                            MilestoneStatus.PENDING,
                            MilestoneStatus.IN_PROGRESS,
                            MilestoneStatus.COMPLETED,
                        ],
                        weights=[30, 45, 25],
                        k=1,
                    )[0]

                elif (
                    collaboration.status
                    == CollaborationStatus.COMPLETED
                ):
                    milestone_status = MilestoneStatus.COMPLETED

                else:
                    continue

                due_time = now + timedelta(
                    days=random.randint(-20, 45)
                )

                completed_at = None

                if milestone_status == MilestoneStatus.COMPLETED:
                    completed_time = now - timedelta(
                        days=random.randint(0, 30)
                    )
                    completed_at = completed_time.isoformat()

                milestone = Milestone(
                    collaboration_id=collaboration.id,
                    title=title,
                    description=description,
                    status=milestone_status,
                    sort_order=existing_for_collaboration,
                    due_at=due_time.isoformat(),
                    completed_at=completed_at,
                )

                session.add(milestone)
                await session.flush()

                created += 1
                status_counts[milestone_status] += 1

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        total_count = await session.scalar(
            select(func.count())
            .select_from(Milestone)
            .where(
                Milestone.collaboration_id.in_(
                    collaboration_ids
                )
            )
        )

        print(f"Existing Faker milestones: {existing_count}")
        print(f"Milestones created: {created}")

        for status, count in status_counts.items():
            if count:
                print(f"  {status.value}: {count}")

        print(f"Total Faker milestones: {total_count}")

async def seed_messaging() -> None:
    """Create realistic conversations and messages for Faker users."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        if len(users) < 2:
            print("Not enough Faker users for messaging.")
            return

        user_ids = [user.id for user in users]

        # Identify existing conversations that contain Faker users.
        result = await session.execute(
            select(ConversationParticipant.conversation_id)
            .where(
                ConversationParticipant.user_id.in_(user_ids)
            )
        )
        existing_conversation_ids = set(result.scalars().all())

        existing_count = len(existing_conversation_ids)

        print()
        print("Messaging")
        print("---------")

        if existing_count >= CONVERSATION_COUNT:
            print(
                f"Existing Faker conversations: {existing_count}"
            )
            print("Skipping conversation creation.")
            return

        conversations_needed = (
            CONVERSATION_COUNT - existing_count
        )

        created_conversations = []

        try:
            for index in range(conversations_needed):
                # About 20% of conversations are group chats.
                is_group = random.random() < 0.20

                if is_group:
                    participant_count = random.choice([3, 4])
                else:
                    participant_count = 2

                participants = random.sample(
                    users,
                    participant_count,
                )

                conversation = Conversation(
                    title=(
                        f"Creator Group {index + 1}"
                        if is_group
                        else None
                    ),
                    is_group=is_group,
                )

                session.add(conversation)
                await session.flush()

                conversation_participant_rows = []

                for participant_index, user in enumerate(
                    participants
                ):
                    participant_row = ConversationParticipant(
                        conversation_id=conversation.id,
                        user_id=user.id,
                        is_admin=(
                            is_group
                            and participant_index == 0
                        ),
                        last_read_at=None,
                        is_muted=(
                            random.random() < 0.08
                        ),
                    )

                    session.add(participant_row)
                    conversation_participant_rows.append(
                        participant_row
                    )

                created_conversations.append(
                    (
                        conversation,
                        participants,
                        conversation_participant_rows,
                    )
                )

            await session.flush()

            # Spread the requested messages across conversations.
            base_messages = (
                MESSAGE_COUNT // len(created_conversations)
            )
            extra_messages = (
                MESSAGE_COUNT % len(created_conversations)
            )

            messages_created = 0
            now = datetime.now(timezone.utc)

            for conversation_index, (
                conversation,
                participants,
                conversation_participant_rows,
            ) in enumerate(created_conversations):

                message_count = base_messages

                if conversation_index < extra_messages:
                    message_count += 1

                conversation_start = now - timedelta(
                    days=random.randint(1, 45),
                    hours=random.randint(0, 23),
                )

                last_message = None
                last_message_time: datetime | None = None

                for message_index in range(message_count):
                    sender = random.choice(participants)

                    message_time = (
                        conversation_start
                        + timedelta(
                            minutes=(
                                message_index
                                * random.randint(5, 90)
                            )
                        )
                    )

                    body = random.choice(MESSAGE_TEXTS)

                    message = Message(
                        conversation_id=conversation.id,
                        sender_id=sender.id,
                        body=body,
                        content_type="text",
                        attachments=None,
                        client_message_id=(
                            f"seed-{conversation.id}-"
                            f"{message_index + 1}"
                        ),
                        is_edited=False,
                        edited_at=None,
                    )

                    session.add(message)

                    last_message = message
                    last_message_time = message_time
                    messages_created += 1

                if (
                    last_message is not None
                    and last_message_time is not None
                ):
                    conversation.last_message_text = (
                        last_message.body
                    )
                    conversation.last_message_at = (
                        last_message_time.isoformat()
                    )
                    conversation.last_message_by = (
                        last_message.sender_id
                    )

                    # Give participants varied read states.
                    for participant_row in conversation_participant_rows:
                        if random.random() < 0.75:
                            read_offset = random.randint(0, 120)

                            participant_row.last_read_at = (
                                last_message_time
                                - timedelta(minutes=read_offset)
                            ).isoformat()

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        print(
            f"Existing Faker conversations: {existing_count}"
        )
        print(
            f"Conversations created: "
            f"{len(created_conversations)}"
        )
        print(f"Messages created: {messages_created}")

        group_count = sum(
            1
            for conversation, _, _ in created_conversations
            if conversation.is_group
        )

        direct_count = (
            len(created_conversations) - group_count
        )

        print(f"  Direct conversations: {direct_count}")
        print(f"  Group conversations: {group_count}")

async def recalculate_profile_counters() -> None:
    """Recalculate aggregate counters for Faker profiles."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        if not users:
            print()
            print("Profile counters")
            print("----------------")
            print("No Faker users found.")
            return

        user_ids = [user.id for user in users]

        result = await session.execute(
            select(Profile)
            .where(Profile.user_id.in_(user_ids))
        )
        profiles = result.scalars().all()

        try:
            for profile in profiles:
                # Likes received across this creator's posts.
                likes_result = await session.execute(
                    select(func.count(PostLike.id))
                    .join(
                        Post,
                        PostLike.post_id == Post.id,
                    )
                    .where(
                        Post.user_id == profile.user_id,
                        Post.deleted_at.is_(None),
                    )
                )

                profile.total_likes = (
                    likes_result.scalar_one() or 0
                )

                # Count collaborations where this user is
                # actually a participant.
                collaborations_result = await session.execute(
                    select(
                        func.count(
                            CollaborationParticipant.id
                        )
                    )
                    .join(
                        Collaboration,
                        CollaborationParticipant.collaboration_id
                        == Collaboration.id,
                    )
                    .where(
                        CollaborationParticipant.user_id
                        == profile.user_id,
                        Collaboration.deleted_at.is_(None),
                    )
                )

                profile.collaboration_count = (
                    collaborations_result.scalar_one() or 0
                )

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        total_likes = sum(
            profile.total_likes
            for profile in profiles
        )

        total_collaborations = sum(
            profile.collaboration_count
            for profile in profiles
        )

        print()
        print("Profile counters")
        print("----------------")
        print(f"Profiles updated: {len(profiles)}")
        print(f"Total likes received: {total_likes}")
        print(
            "Total collaboration participations: "
            f"{total_collaborations}"
        )

async def seed_notifications() -> None:
    """Create notifications from existing Faker activity."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        if not users:
            print()
            print("Notifications")
            print("-------------")
            print("No Faker users found.")
            return

        user_ids = [user.id for user in users]
        user_map = {
            user.id: user
            for user in users
        }

        result = await session.execute(
            select(Notification)
            .where(Notification.user_id.in_(user_ids))
            .where(
                Notification.event_key.like("seed:%")
            )
        )
        existing_notifications = result.scalars().all()

        existing_count = len(existing_notifications)

        print()
        print("Notifications")
        print("-------------")
        print(
            f"Existing Faker notifications: "
            f"{existing_count}"
        )

        if existing_count >= NOTIFICATION_COUNT:
            print("Skipping notification creation.")
            return

        candidates = []

        # -------------------------------------------------
        # Follow notifications
        # -------------------------------------------------

        result = await session.execute(
            select(Follow)
            .where(Follow.follower_id.in_(user_ids))
            .where(Follow.following_id.in_(user_ids))
        )
        follows = result.scalars().all()

        for follow in follows:
            actor = user_map.get(follow.follower_id)

            if actor is None:
                continue

            candidates.append(
                {
                    "user_id": follow.following_id,
                    "type": NotificationType.NEW_FOLLOWER,
                    "title": "New follower",
                    "body": (
                        f"{actor.username} started "
                        f"following you."
                    ),
                    "actor_id": actor.id,
                    "event_key": (
                        f"seed:follow:{follow.id}"
                    ),
                    "data": {
                        "user_id": str(actor.id),
                    },
                }
            )

        # -------------------------------------------------
        # Like notifications
        # -------------------------------------------------

        result = await session.execute(
            select(PostLike, Post)
            .join(
                Post,
                PostLike.post_id == Post.id,
            )
            .where(Post.user_id.in_(user_ids))
            .where(PostLike.user_id.in_(user_ids))
        )
        like_rows = result.all()

        for like, post in like_rows:
            if like.user_id == post.user_id:
                continue

            actor = user_map.get(like.user_id)

            if actor is None:
                continue

            candidates.append(
                {
                    "user_id": post.user_id,
                    "type": NotificationType.NEW_LIKE,
                    "title": "New like",
                    "body": (
                        f"{actor.username} liked "
                        f"your post."
                    ),
                    "actor_id": actor.id,
                    "event_key": (
                        f"seed:like:{like.id}"
                    ),
                    "data": {
                        "post_id": str(post.id),
                    },
                }
            )

        # -------------------------------------------------
        # Comment notifications
        # -------------------------------------------------

        result = await session.execute(
            select(Comment, Post)
            .join(
                Post,
                Comment.post_id == Post.id,
            )
            .where(Post.user_id.in_(user_ids))
            .where(Comment.user_id.in_(user_ids))
        )
        comment_rows = result.all()

        for comment, post in comment_rows:
            if comment.user_id == post.user_id:
                continue

            actor = user_map.get(comment.user_id)

            if actor is None:
                continue

            candidates.append(
                {
                    "user_id": post.user_id,
                    "type": NotificationType.NEW_COMMENT,
                    "title": "New comment",
                    "body": (
                        f"{actor.username} commented "
                        f"on your post."
                    ),
                    "actor_id": actor.id,
                    "event_key": (
                        f"seed:comment:{comment.id}"
                    ),
                    "data": {
                        "post_id": str(post.id),
                        "comment_id": str(comment.id),
                    },
                }
            )

        # -------------------------------------------------
        # Collaboration notifications
        # -------------------------------------------------

        result = await session.execute(
            select(Collaboration)
            .where(
                Collaboration.initiator_id.in_(user_ids)
            )
            .where(Collaboration.deleted_at.is_(None))
        )
        collaborations = result.scalars().all()

        for collaboration in collaborations:
            result = await session.execute(
                select(CollaborationParticipant)
                .where(
                    CollaborationParticipant.collaboration_id
                    == collaboration.id
                )
                .where(
                    CollaborationParticipant.user_id
                    != collaboration.initiator_id
                )
            )
            partners = result.scalars().all()

            initiator = user_map.get(
                collaboration.initiator_id
            )

            if initiator is None:
                continue

            for partner in partners:
                if partner.user_id not in user_map:
                    continue

                candidates.append(
                    {
                        "user_id": partner.user_id,
                        "type": (
                            NotificationType.COLLABORATION_INVITE
                        ),
                        "title": "Collaboration invite",
                        "body": (
                            f"{initiator.username} invited "
                            f"you to collaborate."
                        ),
                        "actor_id": initiator.id,
                        "event_key": (
                            "seed:collaboration-invite:"
                            f"{collaboration.id}:"
                            f"{partner.user_id}"
                        ),
                        "data": {
                            "collaboration_id": str(
                                collaboration.id
                            ),
                        },
                    }
                )

                if collaboration.status in (
                    CollaborationStatus.ACCEPTED,
                    CollaborationStatus.IN_PROGRESS,
                    CollaborationStatus.COMPLETED,
                ):
                    candidates.append(
                        {
                            "user_id": initiator.id,
                            "type": (
                                NotificationType
                                .COLLABORATION_ACCEPTED
                            ),
                            "title": (
                                "Collaboration accepted"
                            ),
                            "body": (
                                "Your collaboration "
                                "invite was accepted."
                            ),
                            "actor_id": partner.user_id,
                            "event_key": (
                                "seed:collaboration-accepted:"
                                f"{collaboration.id}:"
                                f"{partner.user_id}"
                            ),
                            "data": {
                                "collaboration_id": str(
                                    collaboration.id
                                ),
                            },
                        }
                    )

                if (
                    collaboration.status
                    == CollaborationStatus.COMPLETED
                ):
                    candidates.append(
                        {
                            "user_id": partner.user_id,
                            "type": (
                                NotificationType
                                .COLLABORATION_COMPLETED
                            ),
                            "title": (
                                "Collaboration completed"
                            ),
                            "body": (
                                "Your collaboration has "
                                "been completed."
                            ),
                            "actor_id": initiator.id,
                            "event_key": (
                                "seed:collaboration-completed:"
                                f"{collaboration.id}:"
                                f"{partner.user_id}"
                            ),
                            "data": {
                                "collaboration_id": str(
                                    collaboration.id
                                ),
                            },
                        }
                    )

        # -------------------------------------------------
        # Message notifications
        # -------------------------------------------------

        result = await session.execute(
            select(Message)
            .where(Message.sender_id.in_(user_ids))
            .where(Message.deleted_at.is_(None))
        )
        messages = result.scalars().all()

        for message in messages:
            result = await session.execute(
                select(ConversationParticipant)
                .where(
                    ConversationParticipant.conversation_id
                    == message.conversation_id
                )
                .where(
                    ConversationParticipant.user_id
                    != message.sender_id
                )
            )
            recipients = result.scalars().all()

            actor = user_map.get(message.sender_id)

            if actor is None:
                continue

            for recipient in recipients:
                if recipient.user_id not in user_map:
                    continue

                candidates.append(
                    {
                        "user_id": recipient.user_id,
                        "type": NotificationType.MESSAGE,
                        "title": "New message",
                        "body": (
                            f"{actor.username} sent "
                            f"you a message."
                        ),
                        "actor_id": actor.id,
                        "event_key": (
                            f"seed:message:{message.id}:"
                            f"{recipient.user_id}"
                        ),
                        "data": {
                            "conversation_id": str(
                                message.conversation_id
                            ),
                            "message_id": str(
                                message.id
                            ),
                        },
                    }
                )

        # Remove events that already have notifications.
        existing_event_keys = {
            notification.event_key
            for notification in existing_notifications
            if notification.event_key is not None
        }

        candidates = [
            candidate
            for candidate in candidates
            if candidate["event_key"]
            not in existing_event_keys
        ]

        random.shuffle(candidates)

        needed = (
            NOTIFICATION_COUNT - existing_count
        )

        selected = candidates[:needed]

        try:
            now = datetime.now(timezone.utc)

            for candidate in selected:
                is_read = random.random() < 0.65

                read_at = None

                if is_read:
                    read_at = (
                        now
                        - timedelta(
                            minutes=random.randint(
                                1,
                                10080,
                            )
                        )
                    ).isoformat()

                notification = Notification(
                    user_id=candidate["user_id"],
                    type=candidate["type"],
                    title=candidate["title"],
                    body=candidate["body"],
                    data=candidate["data"],
                    event_key=candidate["event_key"],
                    channel=NotificationChannel.IN_APP,
                    is_read=is_read,
                    read_at=read_at,
                    actor_id=candidate["actor_id"],
                )

                session.add(notification)

            await session.commit()

        except Exception:
            await session.rollback()
            raise

        total_count = existing_count + len(selected)
        read_count = sum(
            1
            for candidate in selected
            # Read state was generated above, so we'll
            # query the actual result below instead.
        )

        result = await session.execute(
            select(func.count(Notification.id))
            .where(Notification.user_id.in_(user_ids))
            .where(
                Notification.event_key.like("seed:%")
            )
            .where(Notification.is_read.is_(False))
        )
        unread_count = result.scalar_one() or 0

        print(
            f"Notifications created: {len(selected)}"
        )
        print(
            f"Total Faker notifications: {total_count}"
        )
        print(
            f"Unread Faker notifications: {unread_count}"
        )

async def seed_reputation() -> None:
    """Seed endorsements, reputation scores, and badges."""

    async with async_session_factory() as session:
        result = await session.execute(
            select(User)
            .where(User.email.like(f"%@{FAKE_EMAIL_DOMAIN}"))
            .order_by(User.email)
        )
        users = result.scalars().all()

        if not users:
            print()
            print("Reputation")
            print("----------")
            print("No Faker users found.")
            return

        user_ids = [user.id for user in users]

        print()
        print("Reputation")
        print("----------")

        # ---------------------------------------------
        # Badge definitions
        # ---------------------------------------------

        badge_map = {}

        for definition in BADGE_DEFINITIONS:
            result = await session.execute(
                select(Badge).where(
                    Badge.name == definition["name"]
                )
            )
            badge = result.scalar_one_or_none()

            if badge is None:
                badge = Badge(
                    name=definition["name"],
                    description=definition["description"],
                    icon_url=None,
                    category=definition["category"],
                    tier=definition["tier"],
                    criteria=definition["criteria"],
                )
                session.add(badge)
                await session.flush()

            badge_map[badge.name] = badge

        # ---------------------------------------------
        # Endorsements
        # ---------------------------------------------

        result = await session.execute(
            select(Endorsement)
            .where(Endorsement.endorser_id.in_(user_ids))
            .where(Endorsement.endorsee_id.in_(user_ids))
        )
        existing_endorsements = result.scalars().all()

        existing_pairs = {
            (
                endorsement.endorser_id,
                endorsement.endorsee_id,
                endorsement.collaboration_id,
            )
            for endorsement in existing_endorsements
        }

        result = await session.execute(
            select(Collaboration)
            .where(
                Collaboration.initiator_id.in_(user_ids)
            )
            .where(Collaboration.deleted_at.is_(None))
        )
        collaborations = result.scalars().all()

        endorsement_candidates = []

        for collaboration in collaborations:
            result = await session.execute(
                select(CollaborationParticipant)
                .where(
                    CollaborationParticipant.collaboration_id
                    == collaboration.id
                )
            )
            participants = result.scalars().all()

            participant_ids = [
                participant.user_id
                for participant in participants
                if participant.user_id in user_ids
            ]

            if len(participant_ids) < 2:
                continue

            for endorser_id in participant_ids:
                for endorsee_id in participant_ids:
                    if endorser_id == endorsee_id:
                        continue

                    key = (
                        endorser_id,
                        endorsee_id,
                        collaboration.id,
                    )

                    if key in existing_pairs:
                        continue

                    endorsement_candidates.append(
                        (
                            endorser_id,
                            endorsee_id,
                            collaboration.id,
                        )
                    )

        random.shuffle(endorsement_candidates)

        endorsements_needed = max(
            0,
            ENDORSEMENT_COUNT
            - len(existing_endorsements),
        )

        created_endorsements = 0

        for (
            endorser_id,
            endorsee_id,
            collaboration_id,
        ) in endorsement_candidates[:endorsements_needed]:
            session.add(
                Endorsement(
                    endorser_id=endorser_id,
                    endorsee_id=endorsee_id,
                    category=random.choice(
                        [
                            "collaboration",
                            "creativity",
                            "reliability",
                            "communication",
                        ]
                    ),
                    comment=random.choice(
                        ENDORSEMENT_COMMENTS
                    ),
                    rating=random.choices(
                        [3, 4, 5],
                        weights=[10, 35, 55],
                        k=1,
                    )[0],
                    collaboration_id=collaboration_id,
                )
            )
            created_endorsements += 1

        await session.flush()

        # ---------------------------------------------
        # Reputation scores
        # ---------------------------------------------

        for user in users:
            result = await session.execute(
                select(func.count(Endorsement.id))
                .where(
                    Endorsement.endorsee_id == user.id
                )
            )
            endorsement_count = (
                result.scalar_one() or 0
            )

            result = await session.execute(
                select(
                    func.avg(Endorsement.rating)
                )
                .where(
                    Endorsement.endorsee_id == user.id
                )
            )
            average_rating = (
                result.scalar_one() or 0.0
            )

            result = await session.execute(
                select(
                    func.count(
                        CollaborationParticipant.id
                    )
                )
                .join(
                    Collaboration,
                    CollaborationParticipant.collaboration_id
                    == Collaboration.id,
                )
                .where(
                    CollaborationParticipant.user_id
                    == user.id,
                    Collaboration.status
                    == CollaborationStatus.COMPLETED,
                    Collaboration.deleted_at.is_(None),
                )
            )
            completed_count = (
                result.scalar_one() or 0
            )

            collaboration_score = min(
                100.0,
                float(completed_count) * 20.0
                + float(endorsement_count) * 8.0,
            )

            content_quality_score = min(
                100.0,
                float(average_rating) * 20.0
                if average_rating
                else random.uniform(45.0, 70.0),
            )

            community_score = min(
                100.0,
                45.0 + float(endorsement_count) * 8.0,
            )

            reliability_score = min(
                100.0,
                55.0 + float(completed_count) * 12.0,
            )

            overall_score = round(
                (
                    collaboration_score
                    + content_quality_score
                    + community_score
                    + reliability_score
                )
                / 4.0,
                2,
            )

            result = await session.execute(
                select(ReputationScore)
                .where(
                    ReputationScore.user_id == user.id
                )
            )
            reputation = result.scalar_one_or_none()

            if reputation is None:
                reputation = ReputationScore(
                    user_id=user.id,
                )
                session.add(reputation)

            reputation.overall_score = overall_score
            reputation.collaboration_score = round(
                collaboration_score,
                2,
            )
            reputation.content_quality_score = round(
                content_quality_score,
                2,
            )
            reputation.community_score = round(
                community_score,
                2,
            )
            reputation.reliability_score = round(
                reliability_score,
                2,
            )
            reputation.total_endorsements = (
                endorsement_count
            )
            reputation.completed_collaborations = (
                completed_count
            )

            if completed_count > 0:
                reputation.on_time_delivery_rate = round(
                    random.uniform(82.0, 100.0),
                    2,
                )
            else:
                reputation.on_time_delivery_rate = 0.0

            reputation.score_version = 1
            reputation.computed_at = (
                datetime.now(timezone.utc).isoformat()
            )

        await session.flush()

        # ---------------------------------------------
        # Badge awards based on actual data
        # ---------------------------------------------

        result = await session.execute(
            select(UserBadge)
            .where(UserBadge.user_id.in_(user_ids))
        )
        existing_user_badges = result.scalars().all()

        existing_badge_pairs = {
            (item.user_id, item.badge_id)
            for item in existing_user_badges
        }

        badges_awarded = 0

        for user in users:
            result = await session.execute(
                select(Profile).where(
                    Profile.user_id == user.id
                )
            )
            profile = result.scalar_one()

            result = await session.execute(
                select(func.count(Post.id))
                .where(
                    Post.user_id == user.id,
                    Post.status
                    == ContentStatus.PUBLISHED,
                    Post.deleted_at.is_(None),
                )
            )
            post_count = result.scalar_one() or 0

            result = await session.execute(
                select(
                    func.count(Milestone.id)
                )
                .join(
                    Collaboration,
                    Milestone.collaboration_id
                    == Collaboration.id,
                )
                .join(
                    CollaborationParticipant,
                    CollaborationParticipant.collaboration_id
                    == Collaboration.id,
                )
                .where(
                    CollaborationParticipant.user_id
                    == user.id,
                    Milestone.status
                    == MilestoneStatus.COMPLETED,
                )
            )
            completed_milestones = (
                result.scalar_one() or 0
            )

            earned_badges = []

            if profile.collaboration_count >= 1:
                earned_badges.append(
                    badge_map["First Collaboration"]
                )

            if profile.collaboration_count >= 2:
                earned_badges.append(
                    badge_map["Active Collaborator"]
                )

            if profile.follower_count >= 5:
                earned_badges.append(
                    badge_map["Community Builder"]
                )

            if post_count >= 3:
                earned_badges.append(
                    badge_map["Content Creator"]
                )

            if profile.total_likes >= 15:
                earned_badges.append(
                    badge_map["Audience Favorite"]
                )

            if completed_milestones >= 1:
                earned_badges.append(
                    badge_map["Milestone Maker"]
                )

            for badge in earned_badges:
                pair = (user.id, badge.id)

                if pair in existing_badge_pairs:
                    continue

                session.add(
                    UserBadge(
                        user_id=user.id,
                        badge_id=badge.id,
                        awarded_at=(
                            datetime.now(
                                timezone.utc
                            ).isoformat()
                        ),
                        awarded_by=None,
                    )
                )

                existing_badge_pairs.add(pair)
                badges_awarded += 1

        await session.commit()

        result = await session.execute(
            select(func.count(ReputationScore.id))
            .where(
                ReputationScore.user_id.in_(user_ids)
            )
        )
        reputation_count = result.scalar_one() or 0

        result = await session.execute(
            select(func.count(Endorsement.id))
            .where(
                Endorsement.endorser_id.in_(user_ids)
            )
            .where(
                Endorsement.endorsee_id.in_(user_ids)
            )
        )
        endorsement_count = result.scalar_one() or 0

        result = await session.execute(
            select(func.count(UserBadge.id))
            .where(UserBadge.user_id.in_(user_ids))
        )
        user_badge_count = result.scalar_one() or 0

        print(
            f"Reputation scores: {reputation_count}"
        )
        print(
            f"Endorsements created: "
            f"{created_endorsements}"
        )
        print(
            f"Total Faker endorsements: "
            f"{endorsement_count}"
        )
        print(f"Badge definitions: {len(badge_map)}")
        print(f"New badge awards: {badges_awarded}")
        print(
            f"Total Faker badge awards: "
            f"{user_badge_count}"
        )

async def seed_analytics() -> None:
    """Build recommendation and analytics data from seeded activity."""

    async with async_session_factory() as session:
        print()
        print("Analytics + Recommendation Signals")
        print("----------------------------------")

        # -------------------------------------------------
        # Load Faker users and posts
        # -------------------------------------------------

        result = await session.execute(
            select(User)
            .where(
                User.email.like(
                    f"%@{FAKE_EMAIL_DOMAIN}"
                )
            )
            .order_by(User.email)
        )
        users = result.scalars().all()

        if not users:
            print("No Faker users found.")
            return

        user_ids = [user.id for user in users]
        user_map = {
            user.id: user
            for user in users
        }

        result = await session.execute(
            select(Post)
            .where(Post.user_id.in_(user_ids))
            .where(Post.deleted_at.is_(None))
        )
        posts = result.scalars().all()

        post_map = {
            post.id: post
            for post in posts
        }

        if not posts:
            print("No Faker posts found.")
            return

        # -------------------------------------------------
        # Idempotence
        #
        # If Faker analytics already exist, don't duplicate
        # this append-only event data.
        # -------------------------------------------------

        result = await session.execute(
            select(func.count(AnalyticsEvent.id))
            .where(
                AnalyticsEvent.user_id.in_(user_ids)
            )
            .where(
                AnalyticsEvent.event_metadata[
                    "seed"
                ].as_boolean()
                == True
            )
        )
        existing_seed_events = (
            result.scalar_one() or 0
        )

        result = await session.execute(
            select(func.count(InteractionSignal.id))
            .where(
                InteractionSignal.user_id.in_(user_ids)
            )
        )
        existing_signals = result.scalar_one() or 0

        if (
            existing_seed_events > 0
            or existing_signals > 0
        ):
            print(
                "Existing Faker analytics detected."
            )
            print(
                f"Interaction signals: "
                f"{existing_signals}"
            )
            print(
                f"Seed analytics events: "
                f"{existing_seed_events}"
            )
            print("Skipping analytics seed.")
            return

        # -------------------------------------------------
        # Recommendation signals
        # -------------------------------------------------

        signals_created = 0

        # Watches generate VIEW + WATCH_DURATION and
        # optionally COMPLETION / REWATCH.

        result = await session.execute(
            select(PostWatch)
            .where(PostWatch.user_id.in_(user_ids))
            .where(PostWatch.post_id.in_(post_map.keys()))
        )
        watches = result.scalars().all()

        for watch in watches:
            post = post_map.get(watch.post_id)

            if post is None:
                continue

            # Existing watch schema already represents
            # an actual viewing event.
            session.add(
                InteractionSignal(
                    user_id=watch.user_id,
                    post_id=post.id,
                    creator_id=post.user_id,
                    stream_session_id=None,
                    paid_delivery_id=None,
                    paid_campaign_id=None,
                    signal_type=SignalType.VIEW,
                    value=1.0,
                )
            )
            signals_created += 1

            watch_seconds = random.uniform(
                3.0,
                90.0,
            )

            session.add(
                InteractionSignal(
                    user_id=watch.user_id,
                    post_id=post.id,
                    creator_id=post.user_id,
                    stream_session_id=None,
                    paid_delivery_id=None,
                    paid_campaign_id=None,
                    signal_type=(
                        SignalType.WATCH_DURATION
                    ),
                    value=round(
                        watch_seconds,
                        2,
                    ),
                )
            )
            signals_created += 1

            if random.random() < 0.45:
                session.add(
                    InteractionSignal(
                        user_id=watch.user_id,
                        post_id=post.id,
                        creator_id=post.user_id,
                        stream_session_id=None,
                        paid_delivery_id=None,
                        paid_campaign_id=None,
                        signal_type=(
                            SignalType.COMPLETION
                        ),
                        value=1.0,
                    )
                )
                signals_created += 1

            if watch.rewatched:
                session.add(
                    InteractionSignal(
                        user_id=watch.user_id,
                        post_id=post.id,
                        creator_id=post.user_id,
                        stream_session_id=None,
                        paid_delivery_id=None,
                        paid_campaign_id=None,
                        signal_type=(
                            SignalType.REWATCH
                        ),
                        value=1.0,
                    )
                )
                signals_created += 1

        # Likes

        result = await session.execute(
            select(PostLike)
            .where(PostLike.user_id.in_(user_ids))
            .where(PostLike.post_id.in_(post_map.keys()))
        )
        likes = result.scalars().all()

        for like in likes:
            post = post_map.get(like.post_id)

            if post is None:
                continue

            session.add(
                InteractionSignal(
                    user_id=like.user_id,
                    post_id=post.id,
                    creator_id=post.user_id,
                    stream_session_id=None,
                    paid_delivery_id=None,
                    paid_campaign_id=None,
                    signal_type=SignalType.LIKE,
                    value=1.0,
                )
            )
            signals_created += 1

        # Saves

        result = await session.execute(
            select(PostSave)
            .where(PostSave.user_id.in_(user_ids))
            .where(PostSave.post_id.in_(post_map.keys()))
        )
        saves = result.scalars().all()

        for save in saves:
            post = post_map.get(save.post_id)

            if post is None:
                continue

            session.add(
                InteractionSignal(
                    user_id=save.user_id,
                    post_id=post.id,
                    creator_id=post.user_id,
                    stream_session_id=None,
                    paid_delivery_id=None,
                    paid_campaign_id=None,
                    signal_type=SignalType.SAVE,
                    value=1.0,
                )
            )
            signals_created += 1

        # Shares

        result = await session.execute(
            select(PostShare)
            .where(PostShare.user_id.in_(user_ids))
            .where(PostShare.post_id.in_(post_map.keys()))
        )
        shares = result.scalars().all()

        for share in shares:
            post = post_map.get(share.post_id)

            if post is None:
                continue

            session.add(
                InteractionSignal(
                    user_id=share.user_id,
                    post_id=post.id,
                    creator_id=post.user_id,
                    stream_session_id=None,
                    paid_delivery_id=None,
                    paid_campaign_id=None,
                    signal_type=SignalType.SHARE,
                    value=1.0,
                )
            )
            signals_created += 1

        # Follows are creator-level signals.
        # Therefore post_id MUST remain None.

        result = await session.execute(
            select(Follow)
            .where(Follow.follower_id.in_(user_ids))
            .where(Follow.following_id.in_(user_ids))
        )
        follows = result.scalars().all()

        for follow in follows:
            session.add(
                InteractionSignal(
                    user_id=follow.follower_id,
                    post_id=None,
                    creator_id=follow.following_id,
                    stream_session_id=None,
                    paid_delivery_id=None,
                    paid_campaign_id=None,
                    signal_type=SignalType.FOLLOW,
                    value=1.0,
                )
            )
            signals_created += 1

        # A small amount of explicit negative feedback
        # helps recommendation testing.

        negative_pairs = []

        for user in users:
            candidate_posts = [
                post
                for post in posts
                if post.user_id != user.id
            ]

            if not candidate_posts:
                continue

            for post in random.sample(
                candidate_posts,
                min(
                    3,
                    len(candidate_posts),
                ),
            ):
                negative_pairs.append(
                    (user, post)
                )

        random.shuffle(negative_pairs)

        selected_negative_pairs = (
            negative_pairs[
                :NOT_INTERESTED_COUNT
            ]
        )

        for user, post in selected_negative_pairs:
            session.add(
                InteractionSignal(
                    user_id=user.id,
                    post_id=post.id,
                    creator_id=post.user_id,
                    stream_session_id=None,
                    paid_delivery_id=None,
                    paid_campaign_id=None,
                    signal_type=(
                        SignalType.NOT_INTERESTED
                    ),
                    value=1.0,
                )
            )
            signals_created += 1

        await session.flush()

        # -------------------------------------------------
        # General analytics events
        # -------------------------------------------------

        analytics_service = AnalyticsEventService(
            session
        )

        events_created = 0

        async def track_seed_event(
            *,
            event_type: EventType,
            user=None,
            post=None,
            target_user=None,
            session_id: str | None = None,
            duration_ms: int | None = None,
            metadata: dict | None = None,
        ) -> None:
            nonlocal events_created

            seed_metadata = {
                "seed": True,
                "source": "mock_seed",
            }

            if metadata:
                seed_metadata.update(metadata)

            event = await analytics_service.track_event(
                event_type=event_type,
                user=user,
                post=post,
                target_user=target_user,
                session_id=session_id,
                duration_ms=duration_ms,
                metadata=seed_metadata,
            )

            if event is not None:
                events_created += 1

        # Likes

        for like in likes:
            user = user_map.get(like.user_id)
            post = post_map.get(like.post_id)

            if user is None or post is None:
                continue

            await track_seed_event(
                event_type=EventType.LIKE_CREATED,
                user=user,
                post=post,
            )

        # Saves

        for save in saves:
            user = user_map.get(save.user_id)
            post = post_map.get(save.post_id)

            if user is None or post is None:
                continue

            await track_seed_event(
                event_type=EventType.SAVE_CREATED,
                user=user,
                post=post,
            )

        # Shares

        for share in shares:
            user = user_map.get(share.user_id)
            post = post_map.get(share.post_id)

            if user is None or post is None:
                continue

            await track_seed_event(
                event_type=EventType.SHARE_CREATED,
                user=user,
                post=post,
            )

        # Follows

        for follow in follows:
            follower = user_map.get(
                follow.follower_id
            )
            followed = user_map.get(
                follow.following_id
            )

            if follower is None or followed is None:
                continue

            await track_seed_event(
                event_type=EventType.FOLLOW_CREATED,
                user=follower,
                target_user=followed,
            )

        # Comments

        result = await session.execute(
            select(Comment)
            .where(Comment.user_id.in_(user_ids))
            .where(Comment.post_id.in_(post_map.keys()))
        )
        comments = result.scalars().all()

        for comment in comments:
            user = user_map.get(comment.user_id)
            post = post_map.get(comment.post_id)

            if user is None or post is None:
                continue

            await track_seed_event(
                event_type=(
                    EventType.COMMENT_CREATED
                ),
                user=user,
                post=post,
            )

        # Convert a representative portion of watches
        # into general video analytics events.

        for watch in watches:
            user = user_map.get(watch.user_id)
            post = post_map.get(watch.post_id)

            if user is None or post is None:
                continue

            duration_ms = random.randint(
                3000,
                90000,
            )

            await track_seed_event(
                event_type=EventType.VIDEO_VIEWED,
                user=user,
                post=post,
                duration_ms=duration_ms,
                metadata={
                    "feed": "for_you",
                },
            )

            if random.random() < 0.45:
                await track_seed_event(
                    event_type=(
                        EventType.VIDEO_COMPLETED
                    ),
                    user=user,
                    post=post,
                    duration_ms=duration_ms,
                    metadata={
                        "feed": "for_you",
                    },
                )

        # Profile views

        for _ in range(PROFILE_VIEW_EVENT_COUNT):
            viewer = random.choice(users)

            candidates = [
                user
                for user in users
                if user.id != viewer.id
            ]

            target = random.choice(candidates)

            await track_seed_event(
                event_type=EventType.PROFILE_VIEWED,
                user=viewer,
                target_user=target,
            )

        # Search activity

        search_terms = [
            "music",
            "gaming",
            "art",
            "tech",
            "travel",
            "fitness",
            "fashion",
            "cooking",
            "business",
            "collaboration",
            "creators",
            "video editing",
            "photography",
            "streaming",
        ]

        for _ in range(SEARCH_EVENT_COUNT):
            user = random.choice(users)

            await track_seed_event(
                event_type=(
                    EventType.SEARCH_PERFORMED
                ),
                user=user,
                metadata={
                    "query": random.choice(
                        search_terms
                    ),
                },
            )

        # Negative-feedback analytics mirrors the
        # recommendation signals created above.

        for user, post in selected_negative_pairs:
            await track_seed_event(
                event_type=(
                    EventType.NOT_INTERESTED
                ),
                user=user,
                post=post,
                metadata={
                    "feed": "for_you",
                },
            )

        # Notification opens

        result = await session.execute(
            select(Notification)
            .where(Notification.user_id.in_(user_ids))
            .where(Notification.is_read.is_(True))
        )
        read_notifications = result.scalars().all()

        for notification in read_notifications:
            user = user_map.get(
                notification.user_id
            )

            if user is None:
                continue

            await track_seed_event(
                event_type=(
                    EventType.NOTIFICATION_OPENED
                ),
                user=user,
                metadata={
                    "notification_type": (
                        notification.type.value
                    ),
                },
            )

        await session.commit()

        # -------------------------------------------------
        # Verification
        # -------------------------------------------------

        result = await session.execute(
            select(func.count(InteractionSignal.id))
            .where(
                InteractionSignal.user_id.in_(
                    user_ids
                )
            )
        )
        total_signals = result.scalar_one() or 0

        result = await session.execute(
            select(func.count(AnalyticsEvent.id))
            .where(
                AnalyticsEvent.user_id.in_(user_ids)
            )
            .where(
                AnalyticsEvent.event_metadata[
                    "seed"
                ].as_boolean()
                == True
            )
        )
        total_seed_events = (
            result.scalar_one() or 0
        )

        print(
            f"Interaction signals created: "
            f"{signals_created}"
        )
        print(
            f"Total Faker interaction signals: "
            f"{total_signals}"
        )
        print(
            f"Analytics events created: "
            f"{events_created}"
        )
        print(
            f"Total Faker analytics events: "
            f"{total_seed_events}"
        )
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("ConnextionZ Mock Data Generator")
    print("===============================")

    _, existing_fake_users = await show_existing_data()

    if existing_fake_users:
        print()
        print(
            "Faker users already exist. "
            "Skipping user creation."
        )

        await assign_profile_categories()
        await seed_posts()
        await seed_comments()
        await seed_follows()
        await seed_post_engagement()
        await seed_comment_likes()
        await seed_collaborations()
        await seed_milestones()
        await seed_messaging()
        await recalculate_profile_counters()
        await seed_notifications()
        await seed_reputation()
        await seed_analytics()
        return

    print()
    print("This run will create:")
    print(f"  Users:    {USER_COUNT}")
    print(f"  Profiles: {USER_COUNT}")
    print()
    print("Existing non-seed records will not be modified.")

    confirmation = input(
        "\nType SEED to create the mock users: "
    ).strip()

    if confirmation != "SEED":
        print()
        print("Cancelled. No data was modified.")
        return

    await seed_users_and_profiles()

    print()
    print("Seed complete.")
    print(f"Test login password: {FAKE_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())