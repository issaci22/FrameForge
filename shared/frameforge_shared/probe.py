"""Async ffprobe wrapper."""

from __future__ import annotations

import asyncio
import json
import os

from .media import MediaInfo, parse_ffprobe
from .tools import ffprobe_path

PROBE_TIMEOUT_SECONDS = 120
DECODE_CHECK_SECONDS = 5
DECODE_CHECK_TIMEOUT = 180


class ProbeError(Exception):
    """ffprobe could not read the file."""


async def probe_file(path: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> MediaInfo:
    args = [
        ffprobe_path(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as exc:
        raise ProbeError("ffprobe is not installed or not on PATH") from exc
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ProbeError(f"ffprobe timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        message = stderr.decode(errors="replace").strip().splitlines()
        raise ProbeError(message[-1] if message else f"ffprobe exited with code {proc.returncode}")
    try:
        data = json.loads(stdout.decode(errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc
    if not data.get("format"):
        raise ProbeError("ffprobe found no container information")
    info = parse_ffprobe(data, os.path.basename(path))
    if not info.size:
        try:
            info.size = os.path.getsize(path)
        except OSError:
            pass
    return info


async def decode_check(path: str, from_end: bool, seconds: int = DECODE_CHECK_SECONDS) -> str | None:
    """Decode a few seconds at the start or end of a file. Returns an error message, or None when it decodes."""
    from .ffmpeg_builder import decode_check_args  # the builder imports this package's media model

    try:
        proc = await asyncio.create_subprocess_exec(*decode_check_args(path, from_end, seconds), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
    except FileNotFoundError:
        return "FFmpeg is not installed"
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=DECODE_CHECK_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"decoding took longer than {DECODE_CHECK_TIMEOUT}s"
    if proc.returncode != 0:
        lines = [ln for ln in stderr.decode(errors="replace").splitlines() if ln.strip()]
        return lines[-1] if lines else f"exit code {proc.returncode}"
    return None
