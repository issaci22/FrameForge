"""Shared fixtures and builders for the FrameForge test suite."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from frameforge_shared import protocol as P
from frameforge_shared.media import AudioStream, MediaInfo, SubtitleStream, VideoStream
from frameforge_shared.tools import ffmpeg_path, ffprobe_path

HAVE_FFMPEG = bool(shutil.which(ffmpeg_path()) and shutil.which(ffprobe_path()))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")


# ---------------------------------------------------------------------------
# Media builders
# ---------------------------------------------------------------------------


def make_video(**overrides: Any) -> VideoStream:
    base: dict[str, Any] = {"index": 0, "codec": "h264", "width": 1920, "height": 1080, "fps": 60.0, "pix_fmt": "yuv420p", "bit_depth": 8}
    base.update(overrides)
    return VideoStream(**base)


def make_media(video: VideoStream | None = None, **overrides: Any) -> MediaInfo:
    base: dict[str, Any] = {
        "format_name": "matroska,webm",
        "container": "mkv",
        "duration": 600.0,
        "size": 2_000_000_000,
        "video": video if video is not None else make_video(),
        "audio": [AudioStream(index=1, codec="aac", channels=2)],
    }
    base.update(overrides)
    return MediaInfo(**base)


def sub(index: int, codec: str) -> SubtitleStream:
    return SubtitleStream(index=index, codec=codec)


def make_caps(encoders: list[tuple[str, str, str]] = (("libx265", "hevc", "cpu"),), decoders: list[tuple[str, str]] = (), **overrides: Any) -> P.NodeCapabilities:
    """``encoders`` are (name, codec, backend) triples, all verified. ``decoders`` are (backend, codec)."""
    return P.NodeCapabilities(
        engines={"ffmpeg": True, "handbrake": False},
        encoders=[P.EncoderCapability(name=n, codec=c, backend=b, verified=True) for n, c, b in encoders],
        decoders=[P.DecoderCapability(backend=b, codec=c, verified=True) for b, c in decoders],
        **overrides,
    )


ALL_CPU = [("libx264", "h264", "cpu"), ("libx265", "hevc", "cpu"), ("libsvtav1", "av1", "cpu")]


# ---------------------------------------------------------------------------
# Real media (ffmpeg-backed tests only)
# ---------------------------------------------------------------------------


def make_clip(path: Path, seconds: float = 2.0, *, size: str = "320x240", audio: bool = True, tag: str | None = None, vcodec: str = "libx264") -> Path:
    """Render a tiny synthetic test clip with ffmpeg."""
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    args += ["-c:v", vcodec, "-pix_fmt", "yuv420p"]
    if tag:
        args += ["-metadata", f"FRAMEFORGE={tag}"]
    args += [str(path)]
    subprocess.run(args, check=True, capture_output=True)
    return path


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A fresh, migrated SQLite database under tmp_path, wired into the server's session factory."""
    from frameforge_server import config
    from frameforge_server.db import session as db_session
    from frameforge_server.services import system_settings

    settings = config.Settings(config_dir=tmp_path / "config")
    settings.config_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(system_settings, "_cache", None)
    db_session.run_migrations(settings)
    db_session.init_engine(settings)
    yield settings
    await db_session.dispose()
