"""Safe FFmpeg process construction and lifecycle handling."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import shutil
from urllib.parse import parse_qsl, urlsplit


class FFmpegError(RuntimeError):
    """Base error for FFmpeg lifecycle failures."""


class FFmpegUnavailableError(FFmpegError):
    """Raised when the configured FFmpeg executable cannot be found."""


class FFmpegStartupError(FFmpegError):
    """Raised when FFmpeg exits during its startup window."""


def ffmpeg_available(executable: str) -> bool:
    """Return whether the configured FFmpeg executable is available."""
    candidate = Path(executable)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate.is_file()
    return shutil.which(executable) is not None


def _escape_tee_destination(destination: str) -> str:
    if not destination or any(character in destination for character in ("\x00", "\r", "\n")):
        raise ValueError("Invalid streaming destination")
    return (
        destination.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def build_ffmpeg_args(
    executable: str,
    input_source: str,
    destinations: list[str],
) -> list[str]:
    """Construct FFmpeg arguments for one input and one or more tee outputs."""
    if not input_source.strip() or any(character in input_source for character in ("\x00", "\r", "\n")):
        raise ValueError("Invalid input source")
    if not destinations:
        raise ValueError("At least one streaming destination is required")

    tee_output = "|".join(
        f"[f=flv:onfail=ignore]{_escape_tee_destination(destination)}"
        for destination in destinations
    )
    return [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-re",
        "-i",
        input_source,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-b:v",
        "2500k",
        "-maxrate",
        "2500k",
        "-bufsize",
        "5000k",
        "-r",
        "30",
        "-g",
        "60",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-flags",
        "+global_header",
        "-f",
        "tee",
        tee_output,
    ]


class FFmpegProcess:
    """A running FFmpeg process with bounded, credential-redacted diagnostics."""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        sensitive_values: list[str],
        stderr_limit: int = 100,
    ) -> None:
        self._process = process
        self._sensitive_fragments = self._build_sensitive_fragments(sensitive_values)
        self._stderr_lines: deque[str] = deque(maxlen=stderr_limit)
        self._stderr_task = asyncio.create_task(self._capture_stderr())

    @property
    def return_code(self) -> int | None:
        return self._process.returncode

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._stderr_lines)

    async def wait(self) -> int:
        return await self._process.wait()

    async def terminate(self, timeout_seconds: float) -> int:
        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout_seconds)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
        await self._finish_stderr_capture()
        return self._process.returncode or 0

    async def _capture_stderr(self) -> None:
        if self._process.stderr is None:
            return
        while line := await self._process.stderr.readline():
            self._stderr_lines.append(self._redact(line.decode(errors="replace").strip())[:500])

    def _redact(self, message: str) -> str:
        redacted = message
        for fragment in self._sensitive_fragments:
            redacted = redacted.replace(fragment, "[REDACTED]")
        return redacted

    @staticmethod
    def _build_sensitive_fragments(values: list[str]) -> tuple[str, ...]:
        fragments: set[str] = set(values)
        for value in values:
            parsed = urlsplit(value)
            fragments.update(filter(None, (parsed.username, parsed.password)))
            path_value = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            if path_value:
                fragments.add(path_value)
            fragments.update(query_value for _, query_value in parse_qsl(parsed.query))
        return tuple(sorted((value for value in fragments if len(value) >= 4), key=len, reverse=True))

    async def _finish_stderr_capture(self) -> None:
        if not self._stderr_task.done():
            await self._stderr_task


class FFmpegRunner:
    """Starts FFmpeg without invoking a shell and verifies initial health."""

    def __init__(self, executable: str, startup_timeout_seconds: float) -> None:
        self.executable = executable
        self.startup_timeout_seconds = startup_timeout_seconds

    async def start(self, input_source: str, destinations: list[str]) -> FFmpegProcess:
        if not ffmpeg_available(self.executable):
            raise FFmpegUnavailableError("FFmpeg is unavailable")

        command = build_ffmpeg_args(self.executable, input_source, destinations)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise FFmpegStartupError("FFmpeg process could not be started") from exc
        managed_process = FFmpegProcess(process, [input_source, *destinations])
        try:
            await asyncio.wait_for(
                asyncio.shield(managed_process.wait()),
                timeout=self.startup_timeout_seconds,
            )
        except TimeoutError:
            return managed_process

        await managed_process._finish_stderr_capture()
        raise FFmpegStartupError(
            f"FFmpeg exited during startup with code {managed_process.return_code}"
        )