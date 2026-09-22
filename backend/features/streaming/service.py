"""Database coordination for the FFmpeg streaming lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.session import async_session_factory
from app.models.base import generate_uuidv7
from app.models.streaming import (
    StreamDestination,
    StreamDestinationStatus,
    StreamPlatform,
    StreamSession,
    StreamSessionStatus,
)
from app.models.user import User
from features.streaming.ffmpeg import FFmpegError, FFmpegRunner
from features.streaming.manager import StreamManager
from features.streaming.schemas import (
    StartStreamRequest,
    StreamResponse,
    StreamStatusResponse,
    StopStreamResponse,
)


class StreamNotFoundError(LookupError):
    """Raised when a stream is absent or is not owned by the current user."""


class StreamStartError(RuntimeError):
    """Raised when a persisted stream cannot start its FFmpeg process."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _destination_urls() -> dict[StreamPlatform, str]:
    return {
        StreamPlatform.TWITCH: settings.streaming_twitch_destination_url.get_secret_value(),
        StreamPlatform.YOUTUBE: settings.streaming_youtube_destination_url.get_secret_value(),
        StreamPlatform.KICK: settings.streaming_kick_destination_url.get_secret_value(),
        StreamPlatform.FACEBOOK: settings.streaming_facebook_destination_url.get_secret_value(),
    }


stream_manager = StreamManager(
    runner=FFmpegRunner(settings.ffmpeg_path, settings.ffmpeg_startup_timeout_seconds),
    stop_timeout_seconds=settings.ffmpeg_stop_timeout_seconds,
)


async def _persist_process_exit(
    stream_id: uuid.UUID, return_code: int, stop_requested: bool
) -> None:
    async with async_session_factory() as db:
        result = await db.execute(
            select(StreamSession)
            .options(selectinload(StreamSession.destinations))
            .where(StreamSession.id == stream_id)
        )
        stream = result.scalar_one_or_none()
        if stream is None or stream.status in {
            StreamSessionStatus.ENDED,
            StreamSessionStatus.FAILED,
        }:
            return

        ended_at = _utcnow()
        failed = return_code != 0 and not stop_requested
        stream.status = StreamSessionStatus.FAILED if failed else StreamSessionStatus.ENDED
        stream.ended_at = ended_at
        stream.failure_reason = "Streaming process exited unexpectedly" if failed else None
        for destination in stream.destinations:
            destination.status = (
                StreamDestinationStatus.FAILED if failed else StreamDestinationStatus.ENDED
            )
            destination.ended_at = ended_at
            destination.error_message = "Streaming process failed" if failed else None
        await db.commit()


class StreamingService:
    def __init__(self, db: AsyncSession, manager: StreamManager = stream_manager) -> None:
        self.db = db
        self.manager = manager

    async def start(self, request: StartStreamRequest, owner: User) -> StreamResponse:
        stream = StreamSession(
            id=generate_uuidv7(),
            owner_id=owner.id,
            input_source=request.input_source,
            status=StreamSessionStatus.PENDING,
        )
        destinations = [
            StreamDestination(
                id=generate_uuidv7(),
                stream_session_id=stream.id,
                platform=platform,
                status=StreamDestinationStatus.PENDING,
            )
            for platform in request.platforms
        ]
        stream.destinations = destinations
        self.db.add(stream)
        await self.db.flush()

        urls_by_platform = _destination_urls()
        try:
            await self.manager.start(
                stream_id=stream.id,
                owner_id=owner.id,
                input_source=request.input_source,
                destinations=[urls_by_platform[platform] for platform in request.platforms],
                platforms=[platform.value for platform in request.platforms],
                exit_handler=_persist_process_exit,
            )
        except FFmpegError as exc:
            ended_at = _utcnow()
            stream.status = StreamSessionStatus.FAILED
            stream.ended_at = ended_at
            stream.failure_reason = "Streaming process failed to start"
            for destination in destinations:
                destination.status = StreamDestinationStatus.FAILED
                destination.ended_at = ended_at
                destination.error_message = "Streaming process failed to start"
            await self.db.commit()
            raise StreamStartError("Streaming process failed to start") from exc

        started_at = _utcnow()
        stream.status = StreamSessionStatus.ACTIVE
        stream.started_at = started_at
        for destination in destinations:
            destination.status = StreamDestinationStatus.LIVE
            destination.started_at = started_at
        try:
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            await self.manager.stop(stream.id, owner.id)
            raise
        return self._stream_response(stream)

    async def list(self, owner: User) -> list[StreamResponse]:
        result = await self.db.execute(
            select(StreamSession)
            .options(selectinload(StreamSession.destinations))
            .where(StreamSession.owner_id == owner.id)
            .order_by(StreamSession.created_at.desc())
        )
        return [self._stream_response(stream) for stream in result.scalars().unique().all()]

    async def status(self, stream_id: uuid.UUID, owner: User) -> StreamStatusResponse:
        stream = await self._owned_stream(stream_id, owner.id)
        process = await self.manager.status(stream.id, owner.id)
        return StreamStatusResponse(
            stream_id=stream.id,
            status=stream.status,
            platforms=[destination.platform for destination in stream.destinations],
            process_active=bool(process and process.running),
            started_at=stream.started_at,
            ended_at=stream.ended_at,
        )

    async def stop(self, stream_id: uuid.UUID, owner: User) -> StopStreamResponse:
        stream = await self._owned_stream(stream_id, owner.id)
        if stream.status in {StreamSessionStatus.ENDED, StreamSessionStatus.FAILED}:
            return StopStreamResponse(stream_id=stream.id, status=stream.status)

        stopped = await self.manager.stop(stream.id, owner.id)
        ended_at = _utcnow()
        stream.status = StreamSessionStatus.ENDED if stopped else StreamSessionStatus.FAILED
        stream.ended_at = ended_at
        stream.failure_reason = None if stopped else "Streaming process is no longer available"
        for destination in stream.destinations:
            destination.status = (
                StreamDestinationStatus.ENDED if stopped else StreamDestinationStatus.FAILED
            )
            destination.ended_at = ended_at
            destination.error_message = None if stopped else "Streaming process unavailable"
        await self.db.commit()
        return StopStreamResponse(stream_id=stream.id, status=stream.status)

    async def _owned_stream(self, stream_id: uuid.UUID, owner_id: uuid.UUID) -> StreamSession:
        result = await self.db.execute(
            select(StreamSession)
            .options(selectinload(StreamSession.destinations))
            .where(StreamSession.id == stream_id, StreamSession.owner_id == owner_id)
        )
        stream = result.scalar_one_or_none()
        if stream is None:
            raise StreamNotFoundError("Stream not found")
        return stream

    @staticmethod
    def _stream_response(stream: StreamSession) -> StreamResponse:
        return StreamResponse(
            stream_id=stream.id,
            status=stream.status,
            platforms=[destination.platform for destination in stream.destinations],
            created_at=stream.created_at,
            started_at=stream.started_at,
            ended_at=stream.ended_at,
        )