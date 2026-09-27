"""Parse FFmpeg ``-progress pipe:1`` key=value blocks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProgressSnapshot:
    frame: int | None = None
    fps: float | None = None
    bitrate_kbps: float | None = None
    total_size: int | None = None
    out_time_s: float | None = None
    speed: float | None = None
    ended: bool = False


def _float(value: str) -> float | None:
    value = value.strip().rstrip("x").replace("kbits/s", "")
    if not value or value == "N/A":
        return None
    try:
        return float(value)
    except ValueError:
        return None


class ProgressParser:
    """Feed lines; returns a snapshot each time a ``progress=`` terminator is seen."""

    def __init__(self) -> None:
        self._current = ProgressSnapshot()

    def feed(self, line: str) -> ProgressSnapshot | None:
        if "=" not in line:
            return None
        key, _, value = line.strip().partition("=")
        cur = self._current
        if key == "frame":
            f = _float(value)
            cur.frame = int(f) if f is not None else None
        elif key == "fps":
            cur.fps = _float(value)
        elif key == "bitrate":
            cur.bitrate_kbps = _float(value)
        elif key == "total_size":
            f = _float(value)
            cur.total_size = int(f) if f is not None else None
        elif key in ("out_time_us", "out_time_ms"):
            # Both are microseconds in practice (out_time_ms is historically mislabelled).
            f = _float(value)
            if f is not None and f >= 0:
                cur.out_time_s = f / 1_000_000
        elif key == "speed":
            cur.speed = _float(value)
        elif key == "progress":
            cur.ended = value.strip() == "end"
            snapshot = cur
            self._current = ProgressSnapshot(
                frame=cur.frame, fps=cur.fps, bitrate_kbps=cur.bitrate_kbps, total_size=cur.total_size, out_time_s=cur.out_time_s, speed=cur.speed
            )
            return snapshot
        return None
