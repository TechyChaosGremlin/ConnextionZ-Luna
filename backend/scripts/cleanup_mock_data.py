"""Safely remove Faker mock data from the ConnextionZ development database.

Only users whose email ends with @seed.connextionz.test are considered
Faker users.

Preview:
    python backend/scripts/cleanup_mock_data.py

Delete:
    python backend/scripts/cleanup_mock_data.py --confirm
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, func, or_, select

from app.db.session import async_session_factory

from app.models.analytics import AnalyticsEvent, InteractionSignal
from app.models.collaboration import (
    Collaboration,
    CollaborationParticipant,
    Milestone,
)
from app.models.collaboration_payment import CollaborationPayment
from app.models.content import Comment, Post
from app.models.messaging import (
    Conversation,
    ConversationParticipant,
    Message,
)
from app.models.notification import Notification
from app.models.reputation import Endorsement, ReputationScore, UserBadge
from app.models.social import (
    CommentLike,
    Follow,
    PostLike,
    PostSave,
    PostShare,
    PostWatch,
)
from app.models.user import Profile, User


FAKE_EMAIL_DOMAIN = "seed.connextionz.test"


async def count_rows(session, model, condition) -> int:
    result = await session.execute(
        select(func.count(model.id)).where(condition)
    )
    return result.scalar_one() or 0


async def main(confirm: bool) -> None:
    async with async_session_factory() as session:
        # -------------------------------------------------
        # Locate Faker users
        # -------------------------------------------------

        result = await session.execute(
            select(User.id).where(
                User.email.like(f"%@{FAKE_EMAIL_DOMAIN}")
            )
        )
        fake_user_ids = list(result.scalars().all())

        if not fake_user_ids:
            print("No Faker users found.")
            print("Nothing to clean.")
            return

        # -------------------------------------------------
        # Establish non-Faker safety baseline
        # -------------------------------------------------

        result = await session.execute(
            select(func.count(User.id)).where(
                ~User.email.like(f"%@{FAKE_EMAIL_DOMAIN}")
            )
        )
        non_fake_before = result.scalar_one() or 0

        # -------------------------------------------------
        # Locate Faker-owned posts
        # -------------------------------------------------

        result = await session.execute(
            select(Post.id).where(
                Post.user_id.in_(fake_user_ids)
            )
        )
        fake_post_ids = list(result.scalars().all())

        # -------------------------------------------------
        # Locate Faker collaborations
        # -------------------------------------------------

        result = await session.execute(
            select(Collaboration.id).where(
                Collaboration.initiator_id.in_(
                    fake_user_ids
                )
            )
        )
        fake_collaboration_ids = list(
            result.scalars().all()
        )

        # -------------------------------------------------
        # Locate Faker conversations
        # -------------------------------------------------

        result = await session.execute(
            select(
                ConversationParticipant.conversation_id
            )
            .where(
                ConversationParticipant.user_id.in_(
                    fake_user_ids
                )
            )
            .distinct()
        )
        fake_conversation_ids = list(
            result.scalars().all()
        )

        # -------------------------------------------------
        # PAYMENT SAFETY GUARD
        #
        # Payments use RESTRICT FKs. We never seeded
        # payments, so finding one here means cleanup
        # must stop rather than deleting financial state.
        # -------------------------------------------------

        payment_conditions = [
            CollaborationPayment.payer_user_id.in_(
                fake_user_ids
            ),
            CollaborationPayment.recipient_user_id.in_(
                fake_user_ids
            ),
        ]

        if fake_collaboration_ids:
            payment_conditions.append(
                CollaborationPayment.collaboration_id.in_(
                    fake_collaboration_ids
                )
            )

        result = await session.execute(
            select(func.count(CollaborationPayment.id))
            .where(or_(*payment_conditions))
        )
        payment_count = result.scalar_one() or 0

        if payment_count:
            print()
            print("CLEANUP ABORTED")
            print("---------------")
            print(
                f"Found {payment_count} collaboration "
                "payment record(s) connected to Faker data."
            )
            print(
                "Payments are intentionally NOT deleted "
                "by this cleanup script."
            )
            print(
                "No database changes were made."
            )
            return

        # -------------------------------------------------
        # Preview
        # -------------------------------------------------

        print()
        print("ConnextionZ Faker Cleanup")
        print("-------------------------")
        print(
            f"Faker users: {len(fake_user_ids)}"
        )
        print(
            f"Faker posts: {len(fake_post_ids)}"
        )
        print(
            "Faker collaborations: "
            f"{len(fake_collaboration_ids)}"
        )
        print(
            "Faker conversations: "
            f"{len(fake_conversation_ids)}"
        )
        print(
            f"Non-Faker users protected: "
            f"{non_fake_before}"
        )

        if not confirm:
            print()
            print("PREVIEW ONLY")
            print("No rows were deleted.")
            print()
            print(
                "To actually remove the Faker dataset, run:"
            )
            print(
                "python .\\backend\\scripts\\"
                "cleanup_mock_data.py --confirm"
            )
            return

        print()
        print("Removing Faker data...")

        # -------------------------------------------------
        # Analytics
        # -------------------------------------------------

        await session.execute(
            delete(AnalyticsEvent).where(
                or_(
                    AnalyticsEvent.user_id.in_(
                        fake_user_ids
                    ),
                    AnalyticsEvent.target_user_id.in_(
                        fake_user_ids
                    ),
                    AnalyticsEvent.post_id.in_(
                        fake_post_ids
                    )
                    if fake_post_ids
                    else False,
                )
            )
        )

        await session.execute(
            delete(InteractionSignal).where(
                or_(
                    InteractionSignal.user_id.in_(
                        fake_user_ids
                    ),
                    InteractionSignal.creator_id.in_(
                        fake_user_ids
                    ),
                    InteractionSignal.post_id.in_(
                        fake_post_ids
                    )
                    if fake_post_ids
                    else False,
                )
            )
        )

        # -------------------------------------------------
        # Reputation
        # -------------------------------------------------

        await session.execute(
            delete(UserBadge).where(
                UserBadge.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(ReputationScore).where(
                ReputationScore.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(Endorsement).where(
                or_(
                    Endorsement.endorser_id.in_(
                        fake_user_ids
                    ),
                    Endorsement.endorsee_id.in_(
                        fake_user_ids
                    ),
                )
            )
        )

        # IMPORTANT:
        # Badge definitions themselves are preserved.
        # Categories are also preserved.

        # -------------------------------------------------
        # Notifications
        # -------------------------------------------------

        await session.execute(
            delete(Notification).where(
                or_(
                    Notification.user_id.in_(
                        fake_user_ids
                    ),
                    Notification.actor_id.in_(
                        fake_user_ids
                    ),
                )
            )
        )

        # -------------------------------------------------
        # Messaging
        # -------------------------------------------------

        if fake_conversation_ids:
            await session.execute(
                delete(Message).where(
                    Message.conversation_id.in_(
                        fake_conversation_ids
                    )
                )
            )

            await session.execute(
                delete(
                    ConversationParticipant
                ).where(
                    ConversationParticipant.conversation_id.in_(
                        fake_conversation_ids
                    )
                )
            )

            await session.execute(
                delete(Conversation).where(
                    Conversation.id.in_(
                        fake_conversation_ids
                    )
                )
            )

        # -------------------------------------------------
        # Collaboration data
        # -------------------------------------------------

        if fake_collaboration_ids:
            await session.execute(
                delete(Milestone).where(
                    Milestone.collaboration_id.in_(
                        fake_collaboration_ids
                    )
                )
            )

            await session.execute(
                delete(
                    CollaborationParticipant
                ).where(
                    CollaborationParticipant.collaboration_id.in_(
                        fake_collaboration_ids
                    )
                )
            )

            await session.execute(
                delete(Collaboration).where(
                    Collaboration.id.in_(
                        fake_collaboration_ids
                    )
                )
            )

        # -------------------------------------------------
        # Social engagement
        # -------------------------------------------------

        await session.execute(
            delete(CommentLike).where(
                CommentLike.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(PostWatch).where(
                PostWatch.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(PostShare).where(
                PostShare.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(PostSave).where(
                PostSave.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(PostLike).where(
                PostLike.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(Follow).where(
                or_(
                    Follow.follower_id.in_(
                        fake_user_ids
                    ),
                    Follow.following_id.in_(
                        fake_user_ids
                    ),
                )
            )
        )

        # -------------------------------------------------
        # Content
        # -------------------------------------------------

        if fake_post_ids:
            # Delete all comment likes attached to
            # comments on Faker posts before the posts
            # themselves disappear.

            result = await session.execute(
                select(Comment.id).where(
                    Comment.post_id.in_(
                        fake_post_ids
                    )
                )
            )
            fake_comment_ids = list(
                result.scalars().all()
            )

            if fake_comment_ids:
                await session.execute(
                    delete(CommentLike).where(
                        CommentLike.comment_id.in_(
                            fake_comment_ids
                        )
                    )
                )

            await session.execute(
                delete(Comment).where(
                    Comment.post_id.in_(
                        fake_post_ids
                    )
                )
            )

            await session.execute(
                delete(Post).where(
                    Post.id.in_(
                        fake_post_ids
                    )
                )
            )

        # Remove any remaining comments authored by
        # Faker users on non-Faker posts.
        await session.execute(
            delete(Comment).where(
                Comment.user_id.in_(
                    fake_user_ids
                )
            )
        )

        # -------------------------------------------------
        # Profiles + users
        # -------------------------------------------------

        await session.execute(
            delete(Profile).where(
                Profile.user_id.in_(
                    fake_user_ids
                )
            )
        )

        await session.execute(
            delete(User).where(
                User.id.in_(
                    fake_user_ids
                )
            )
        )

        # -------------------------------------------------
        # SAFETY VERIFICATION BEFORE COMMIT
        # -------------------------------------------------

        result = await session.execute(
            select(func.count(User.id)).where(
                ~User.email.like(
                    f"%@{FAKE_EMAIL_DOMAIN}"
                )
            )
        )
        non_fake_after = result.scalar_one() or 0

        result = await session.execute(
            select(func.count(User.id)).where(
                User.email.like(
                    f"%@{FAKE_EMAIL_DOMAIN}"
                )
            )
        )
        fake_after = result.scalar_one() or 0

        if non_fake_after != non_fake_before:
            await session.rollback()

            print()
            print("SAFETY CHECK FAILED")
            print("-------------------")
            print(
                "Non-Faker user count changed."
            )
            print(
                f"Before: {non_fake_before}"
            )
            print(
                f"After:  {non_fake_after}"
            )
            print()
            print(
                "Transaction rolled back. "
                "Nothing was deleted."
            )
            return

        if fake_after != 0:
            await session.rollback()

            print()
            print("CLEANUP INCOMPLETE")
            print("------------------")
            print(
                f"{fake_after} Faker user(s) "
                "would remain."
            )
            print(
                "Transaction rolled back."
            )
            return

        await session.commit()

        print()
        print("Cleanup complete.")
        print(
            f"Faker users remaining: "
            f"{fake_after}"
        )
        print(
            f"Non-Faker users preserved: "
            f"{non_fake_after}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Safely remove ConnextionZ Faker "
            "development data."
        )
    )

    parser.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Actually delete Faker data. "
            "Without this flag the script "
            "only previews cleanup."
        ),
    )

    args = parser.parse_args()

    import asyncio

    asyncio.run(
        main(confirm=args.confirm)
    )