"""In-process ownership and lifecycle tracking for active FFmpeg streams."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable
import uuid

import structlog

from features.streaming.ffmpeg import FFmpegProcess, FFmpegRunner

logger = structlog.get_logger()
ExitHandler = Callable[[uuid.UUID, int, bool], Awaitable[None]]


@dataclass(frozen=True)
class StreamProcessStatus:
    stream_id: uuid.UUID
    owner_id: uuid.UUID
    platforms: tuple[str, ...]
    running: bool
    return_code: int | None


@dataclass
class ActiveStream:
    process: FFmpegProcess
    owner_id: uuid.UUID
    platforms: tuple[str, ...]
    exit_handler: ExitHandler
    monitor_task: asyncio.Task[None] | None = None
    stop_requested: bool = False


class StreamManager:
    """Tracks active FFmpeg processes by persisted stream session ID."""

    def __init__(self, runner: FFmpegRunner, stop_timeout_seconds: float) -> None:
        self._runner = runner
        self._stop_timeout_seconds = stop_timeout_seconds
        self._active: dict[uuid.UUID, ActiveStream] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        stream_id: uuid.UUID,
        owner_id: uuid.UUID,
        input_source: str,
        destinations: list[str],
        platforms: list[str],
        exit_handler: ExitHandler,
    ) -> StreamProcessStatus:
        process = await self._runner.start(input_source, destinations)
        active = ActiveStream(
            process=process,
            owner_id=owner_id,
            platforms=tuple(platforms),
            exit_handler=exit_handler,
        )
        async with self._lock:
            if stream_id in self._active:
                await process.terminate(self._stop_timeout_seconds)
                raise RuntimeError("Stream session is already active")
            self._active[stream_id] = active
            active.monitor_task = asyncio.create_task(self._monitor(stream_id, active))

        logger.info("Streaming process started", stream_id=str(stream_id), owner_id=str(owner_id))
        return self._status(stream_id, active)

    async def status(
        self, stream_id: uuid.UUID, owner_id: uuid.UUID
    ) -> StreamProcessStatus | None:
        async with self._lock:
            active = self._active.get(stream_id)
            if active is None or active.owner_id != owner_id:
                return None
            return self._status(stream_id, active)

    async def stop(self, stream_id: uuid.UUID, owner_id: uuid.UUID) -> bool:
        async with self._lock:
            active = self._active.get(stream_id)
            if active is None or active.owner_id != owner_id:
                return False
            active.stop_requested = True
            monitor_task = active.monitor_task

        await active.process.terminate(self._stop_timeout_seconds)
        if monitor_task is not None:
            await monitor_task
        return True

    async def cleanup(self) -> None:
        async with self._lock:
            streams = [(stream_id, active.owner_id) for stream_id, active in self._active.items()]
        if streams:
            await asyncio.gather(
                *(self.stop(stream_id, owner_id) for stream_id, owner_id in streams),
                return_exceptions=True,
            )

    async def _monitor(self, stream_id: uuid.UUID, active: ActiveStream) -> None:
        return_code = await active.process.wait()
        async with self._lock:
            if self._active.get(stream_id) is active:
                del self._active[stream_id]

        logger.info(
            "Streaming process exited",
            stream_id=str(stream_id),
            owner_id=str(active.owner_id),
            return_code=return_code,
            stop_requested=active.stop_requested,
        )
        try:
            await active.exit_handler(stream_id, return_code, active.stop_requested)
        except Exception:
            logger.exception("Failed to persist streaming process exit", stream_id=str(stream_id))

    @staticmethod
    def _status(stream_id: uuid.UUID, active: ActiveStream) -> StreamProcessStatus:
        return StreamProcessStatus(
            stream_id=stream_id,
            owner_id=active.owner_id,
            platforms=active.platforms,
            running=active.process.return_code is None,
            return_code=active.process.return_code,
        )