"""Focused tests for safe FFmpeg command construction."""

from types import SimpleNamespace

import pytest

from features.streaming.ffmpeg import FFmpegProcess, build_ffmpeg_args


def test_build_ffmpeg_args_uses_tee_without_a_shell() -> None:
    args = build_ffmpeg_args(
        "ffmpeg-custom",
        "input.mp4",
        ["rtmp://localhost/live/first", "rtmp://localhost/live/second"],
    )

    assert args[0] == "ffmpeg-custom"
    assert args[args.index("-i") + 1] == "input.mp4"
    assert args[args.index("-f") + 1] == "tee"
    assert "[f=flv:onfail=ignore]rtmp://localhost/live/first" in args[-1]
    assert "[f=flv:onfail=ignore]rtmp://localhost/live/second" in args[-1]
    assert "shell=True" not in args


def test_build_ffmpeg_args_escapes_tee_delimiters() -> None:
    args = build_ffmpeg_args("ffmpeg", "input.mp4", ["rtmp://localhost/live/key|backup"])

    assert "key\\|backup" in args[-1]


def test_build_ffmpeg_args_rejects_missing_destinations() -> None:
    try:
        build_ffmpeg_args("ffmpeg", "input.mp4", [])
    except ValueError as exc:
        assert str(exc) == "At least one streaming destination is required"
    else:
        raise AssertionError("Expected an empty destination list to be rejected")


@pytest.mark.asyncio
async def test_diagnostics_redact_input_and_destination_credentials() -> None:
    process = SimpleNamespace(returncode=None, stderr=None)
    managed = FFmpegProcess(
        process,  # type: ignore[arg-type]
        [
            "https://viewer:input-secret@example.test/live.m3u8?token=signed-token",
            "rtmp://example.test/live/private-stream-key",
        ],
    )

    redacted = managed._redact(  # noqa: SLF001 - verifies the security boundary directly
        "input-secret signed-token private-stream-key"
    )

    assert "input-secret" not in redacted
    assert "signed-token" not in redacted
    assert "private-stream-key" not in redacted
    await managed._stderr_task  # noqa: SLF001