import uuid

from sqlalchemy import and_, or_, select

from app.models.social import Follow


class FollowRepository:
    def __init__(self, db):
        self.db = db

    async def are_following_each_other(
        self,
        viewer_id: uuid.UUID,
        creator_id: uuid.UUID,
    ) -> tuple[bool, bool]:
        viewer_follows_creator = await self.db.scalar(
            select(Follow.id).where(
                Follow.follower_id == viewer_id,
                Follow.following_id == creator_id,
            )
        )

        creator_follows_viewer = await self.db.scalar(
            select(Follow.id).where(
                Follow.follower_id == creator_id,
                Follow.following_id == viewer_id,
            )
        )

        return (
            viewer_follows_creator is not None,
            creator_follows_viewer is not None,
        )

    async def are_following_each_other_for_users(
        self,
        viewer_id: uuid.UUID,
        creator_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, tuple[bool, bool]]:
        """Return mutual follow state for several creators in one query."""
        states = {creator_id: (False, False) for creator_id in creator_ids}
        if not creator_ids:
            return states

        result = await self.db.execute(
            select(Follow.follower_id, Follow.following_id).where(
                or_(
                    and_(
                        Follow.follower_id == viewer_id,
                        Follow.following_id.in_(creator_ids),
                    ),
                    and_(
                        Follow.follower_id.in_(creator_ids),
                        Follow.following_id == viewer_id,
                    ),
                )
            )
        )

        mutable_states = {creator_id: [False, False] for creator_id in creator_ids}
        for follower_id, following_id in result.all():
            if follower_id == viewer_id:
                mutable_states[following_id][0] = True
            else:
                mutable_states[follower_id][1] = True

        return {
            creator_id: (state[0], state[1])
            for creator_id, state in mutable_states.items()
        }