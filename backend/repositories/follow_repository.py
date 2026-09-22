import uuid

from sqlalchemy import select

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