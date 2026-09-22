"""Lifecycle tests for active stream process tracking."""

import asyncio
import uuid

import pytest

from features.streaming.manager import StreamManager


class FakeProcess:
    def __init__(self) -> None:
        self.return_code = None
        self._finished = asyncio.Event()

    async def wait(self) -> int:
        await self._finished.wait()
        assert self.return_code is not None
        return self.return_code

    async def terminate(self, timeout_seconds: float) -> int:
        self.return_code = 0
        self._finished.set()
        return 0


class FakeRunner:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process

    async def start(self, input_source: str, destinations: list[str]) -> FakeProcess:
        return self.process


@pytest.mark.asyncio
async def test_manager_tracks_owner_and_stops_stream() -> None:
    process = FakeProcess()
    manager = StreamManager(FakeRunner(process), stop_timeout_seconds=0.1)  # type: ignore[arg-type]
    stream_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    exits: list[tuple[uuid.UUID, int, bool]] = []

    async def on_exit(exited_id: uuid.UUID, code: int, stopped: bool) -> None:
        exits.append((exited_id, code, stopped))

    status = await manager.start(
        stream_id,
        owner_id,
        "input.mp4",
        ["rtmp://localhost/live/test"],
        ["twitch"],
        on_exit,
    )

    assert status.running is True
    assert await manager.status(stream_id, uuid.uuid4()) is None
    assert await manager.stop(stream_id, uuid.uuid4()) is False
    assert await manager.stop(stream_id, owner_id) is True
    assert await manager.status(stream_id, owner_id) is None
    assert exits == [(stream_id, 0, True)]


@pytest.mark.asyncio
async def test_manager_records_unexpected_exit() -> None:
    process = FakeProcess()
    manager = StreamManager(FakeRunner(process), stop_timeout_seconds=0.1)  # type: ignore[arg-type]
    stream_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    exit_seen = asyncio.Event()
    result: list[tuple[int, bool]] = []

    async def on_exit(exited_id: uuid.UUID, code: int, stopped: bool) -> None:
        result.append((code, stopped))
        exit_seen.set()

    await manager.start(
        stream_id,
        owner_id,
        "input.mp4",
        ["rtmp://localhost/live/test"],
        ["youtube"],
        on_exit,
    )
    process.return_code = 1
    process._finished.set()
    await asyncio.wait_for(exit_seen.wait(), timeout=1)

    assert result == [(1, False)]
    assert await manager.status(stream_id, owner_id) is None