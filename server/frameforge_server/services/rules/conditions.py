"""Condition fields, operators, and the typed condition-tree document."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Union

from pydantic import BaseModel, Field

from frameforge_shared.codecs import CODEC_GENERATION

# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------


class Condition(BaseModel):
    type: Literal["condition"] = "condition"
    field: str
    operator: str
    value: Any = None


class ConditionGroup(BaseModel):
    type: Literal["group"] = "group"
    op: Literal["all", "any", "none"] = "all"
    children: list[Union["ConditionGroup", Condition]] = Field(default_factory=list)


ConditionGroup.model_rebuild()


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------


@dataclass
class HardwareSnapshot:
    """What the online nodes can currently do (for hardware-aware conditions)."""

    online_nodes: int = 0
    hw_codecs: set[str] = field(default_factory=set)  # codecs with a verified HW encoder somewhere
    any_codecs: set[str] = field(default_factory=set)  # codecs encodable anywhere (HW or CPU)


@dataclass
class FileContext:
    path: str
    filename: str
    extension: str
    size: int
    mtime: datetime
    container: str | None = None
    video_codec: str | None = None
    audio_codecs: list[str] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    short_side: int | None = None
    fps: float | None = None
    bit_depth: int | None = None
    hdr_format: str | None = None
    bitrate: int | None = None
    duration: float | None = None
    creation_time: datetime | None = None
    audio_count: int = 0
    subtitle_count: int = 0
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    local_now: datetime = field(default_factory=datetime.now)
    hardware: HardwareSnapshot = field(default_factory=HardwareSnapshot)


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

NUMBER_OPS = ["gt", "gte", "lt", "lte", "eq", "neq", "between"]
ENUM_OPS = ["is", "is_not", "in", "not_in"]
TEXT_OPS = ["contains", "not_contains", "starts_with", "ends_with", "glob", "matches"]
BOOL_OPS = ["is_true", "is_false"]
TIME_OPS = ["between"]
DAY_OPS = ["in", "not_in"]

OPERATOR_LABELS = {
    "gt": "is greater than",
    "gte": "is at least",
    "lt": "is less than",
    "lte": "is at most",
    "eq": "equals",
    "neq": "does not equal",
    "between": "is between",
    "is": "is",
    "is_not": "is not",
    "in": "is one of",
    "not_in": "is not one of",
    "contains": "contains",
    "not_contains": "does not contain",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "glob": "matches pattern",
    "matches": "matches regex",
    "is_true": "is yes",
    "is_false": "is no",
}


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    kind: Literal["number", "enum", "text", "bool", "time", "day"]
    group: str
    getter: Callable[[FileContext], Any]
    unit: str | None = None
    options: list[dict[str, Any]] | None = None
    help: str | None = None
    allow_custom: bool = True  # enum fields: may users type values not in options?

    @property
    def operators(self) -> list[str]:
        return {"number": NUMBER_OPS, "enum": ENUM_OPS, "text": TEXT_OPS, "bool": BOOL_OPS, "time": TIME_OPS, "day": DAY_OPS}[self.kind]


def _age_days(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds() / 86400.0


def _opts(*pairs: tuple[Any, str]) -> list[dict[str, Any]]:
    return [{"value": v, "label": label} for v, label in pairs]


_CODEC_OPTIONS = _opts(
    ("h264", "H.264"), ("hevc", "H.265 / HEVC"), ("av1", "AV1"), ("vp9", "VP9"), ("mpeg2video", "MPEG-2"), ("mpeg4", "MPEG-4"), ("prores", "ProRes"), ("dnxhd", "DNxHD")
)
_AUDIO_OPTIONS = _opts(("aac", "AAC"), ("opus", "Opus"), ("mp3", "MP3"), ("ac3", "AC-3"), ("eac3", "E-AC-3"), ("flac", "FLAC"), ("pcm_s16le", "PCM 16-bit"), ("pcm_s24le", "PCM 24-bit"), ("dts", "DTS"))
_CONTAINER_OPTIONS = _opts(("mkv", "MKV"), ("mp4", "MP4"), ("mov", "MOV"), ("flv", "FLV"), ("ts", "MPEG-TS"), ("webm", "WebM"), ("avi", "AVI"))
_RES_OPTIONS = _opts((480, "480p"), (720, "720p"), (1080, "1080p"), (1440, "1440p"), (2160, "4K (2160p)"), (4320, "8K (4320p)"))
_DAY_OPTIONS = _opts((0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun"))

FIELDS: dict[str, FieldDef] = {
    f.key: f
    for f in [
        FieldDef("file_age_days", "File age", "number", "Age", lambda c: _age_days(c.mtime, c.now), unit="days", help="Days since the file was last modified"),
        FieldDef(
            "recorded_age_days",
            "Recording age",
            "number",
            "Age",
            lambda c: _age_days(c.creation_time or c.mtime, c.now),
            unit="days",
            help="Days since the recording date stored in the file (falls back to modification date)",
        ),
        FieldDef("file_size_gb", "File size", "number", "File", lambda c: c.size / 1024**3, unit="GB"),
        FieldDef("extension", "Extension", "enum", "File", lambda c: c.extension.lstrip(".").lower(), options=_opts(("mkv", ".mkv"), ("mp4", ".mp4"), ("mov", ".mov"), ("flv", ".flv"), ("ts", ".ts"))),
        FieldDef("path", "Folder / path", "text", "File", lambda c: c.path, help="Full path of the file"),
        FieldDef("filename", "File name", "text", "File", lambda c: c.filename),
        FieldDef("container", "Container", "enum", "Format", lambda c: c.container, options=_CONTAINER_OPTIONS),
        FieldDef("video_codec", "Video codec", "enum", "Video", lambda c: c.video_codec, options=_CODEC_OPTIONS),
        FieldDef(
            "codec_generation",
            "Codec generation",
            "number",
            "Video",
            lambda c: CODEC_GENERATION.get(c.video_codec or "", 0),
            help="1 = MPEG-2/4, 2 = H.264, 3 = H.265/VP9, 4 = AV1",
        ),
        FieldDef("resolution", "Resolution", "number", "Video", lambda c: c.short_side, unit="p", options=_RES_OPTIONS, help="Short side in pixels, so vertical video counts correctly"),
        FieldDef("frame_rate", "Frame rate", "number", "Video", lambda c: c.fps, unit="fps"),
        FieldDef(
            "dynamic_range",
            "Dynamic range",
            "enum",
            "Video",
            lambda c: c.hdr_format or "SDR",
            options=_opts(("SDR", "SDR"), ("HDR10", "HDR10"), ("HLG", "HLG"), ("Dolby Vision", "Dolby Vision")),
            allow_custom=False,
        ),
        FieldDef("is_hdr", "Is HDR", "bool", "Video", lambda c: c.hdr_format is not None),
        FieldDef("bit_depth", "Bit depth", "number", "Video", lambda c: c.bit_depth, unit="bit"),
        FieldDef(
            "orientation",
            "Orientation",
            "enum",
            "Video",
            lambda c: None if not c.width or not c.height else ("vertical" if c.height > c.width else "horizontal"),
            options=_opts(("horizontal", "Horizontal"), ("vertical", "Vertical")),
            allow_custom=False,
        ),
        FieldDef("bitrate_mbps", "Overall bitrate", "number", "Video", lambda c: (c.bitrate or 0) / 1_000_000 if c.bitrate else None, unit="Mb/s"),
        FieldDef("duration_minutes", "Duration", "number", "Format", lambda c: (c.duration or 0) / 60 if c.duration else None, unit="min"),
        FieldDef("audio_codec", "Audio codec (any track)", "enum", "Audio", lambda c: c.audio_codecs, options=_AUDIO_OPTIONS),
        FieldDef("audio_tracks", "Audio track count", "number", "Audio", lambda c: c.audio_count),
        FieldDef("subtitle_tracks", "Subtitle track count", "number", "Format", lambda c: c.subtitle_count),
        FieldDef(
            "hw_encoder_for",
            "GPU encoder online for",
            "enum",
            "Hardware",
            lambda c: sorted(c.hardware.hw_codecs),
            options=_opts(("h264", "H.264"), ("hevc", "H.265"), ("av1", "AV1")),
            allow_custom=False,
            help="Matches when an online node has a verified hardware encoder for the codec",
        ),
        FieldDef("online_nodes", "Online nodes", "number", "Hardware", lambda c: c.hardware.online_nodes),
        FieldDef("time_of_day", "Time of day", "time", "Schedule", lambda c: c.local_now.strftime("%H:%M"), help="Checked when the rule is evaluated (during scans)"),
        FieldDef("day_of_week", "Day of week", "day", "Schedule", lambda c: c.local_now.weekday(), options=_DAY_OPTIONS),
    ]
}


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------


class ConditionError(ValueError):
    pass


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConditionError(f"'{value}' is not a number") from exc


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return [value]


def _norm(v: Any) -> Any:
    return v.lower() if isinstance(v, str) else v


def _enum_match(actual: Any, wanted: list[Any]) -> bool:
    wanted_n = {_norm(w) for w in wanted}
    if isinstance(actual, list):
        return any(_norm(a) in wanted_n for a in actual)
    return _norm(actual) in wanted_n


def _minutes(hhmm: str) -> int:
    h, m = str(hhmm).split(":", 1)
    return int(h) * 60 + int(m)


def apply_operator(fd: FieldDef, actual: Any, operator: str, value: Any) -> bool:
    if operator not in fd.operators:
        raise ConditionError(f"'{operator}' can't be used with {fd.label}")
    if fd.kind == "bool":
        return bool(actual) if operator == "is_true" else not bool(actual)
    if actual is None or actual == []:
        # Unknown values never satisfy positive comparisons.
        return operator in ("neq", "is_not", "not_in", "not_contains")
    if fd.kind == "number":
        a = float(actual)
        if operator == "between":
            lo, hi = (_num(x) for x in _as_list(value)[:2])
            return lo <= a <= hi
        v = _num(value)
        return {
            "gt": a > v,
            "gte": a >= v,
            "lt": a < v,
            "lte": a <= v,
            "eq": abs(a - v) < 1e-9,
            "neq": abs(a - v) >= 1e-9,
        }[operator]
    if fd.kind == "enum":
        wanted = _as_list(value)
        if operator in ("is", "in"):
            return _enum_match(actual, wanted)
        return not _enum_match(actual, wanted)
    if fd.kind == "text":
        a = str(actual)
        v = str(value or "")
        if operator == "contains":
            return v.lower() in a.lower()
        if operator == "not_contains":
            return v.lower() not in a.lower()
        if operator == "starts_with":
            return a.lower().startswith(v.lower())
        if operator == "ends_with":
            return a.lower().endswith(v.lower())
        if operator == "glob":
            return fnmatch.fnmatch(a.lower(), v.lower())
        try:
            return re.search(v, a, re.IGNORECASE) is not None
        except re.error as exc:
            raise ConditionError(f"Invalid regular expression: {exc}") from exc
    if fd.kind == "time":
        parts = _as_list(value)
        if len(parts) != 2:
            raise ConditionError("Time range needs a start and an end (HH:MM)")
        now_m, start, end = _minutes(actual), _minutes(parts[0]), _minutes(parts[1])
        return start <= now_m < end if start <= end else (now_m >= start or now_m < end)
    if fd.kind == "day":
        days = {int(d) for d in _as_list(value)}
        return (int(actual) in days) if operator == "in" else (int(actual) not in days)
    raise ConditionError(f"Unsupported field kind {fd.kind}")


def validate_tree(tree: ConditionGroup) -> None:
    """Raise ConditionError on unknown fields/operators so bad rules are rejected at save time."""
    for child in tree.children:
        if isinstance(child, ConditionGroup):
            validate_tree(child)
            continue
        fd = FIELDS.get(child.field)
        if fd is None:
            raise ConditionError(f"Unknown field '{child.field}'")
        if child.operator not in fd.operators:
            raise ConditionError(f"'{OPERATOR_LABELS.get(child.operator, child.operator)}' can't be used with {fd.label}")
        if fd.kind == "number" and child.operator != "between":
            _num(child.value)
        if fd.kind == "text" and child.operator == "matches":
            try:
                re.compile(str(child.value))
            except re.error as exc:
                raise ConditionError(f"Invalid regular expression: {exc}") from exc


def field_catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": f.key,
            "label": f.label,
            "kind": f.kind,
            "group": f.group,
            "unit": f.unit,
            "options": f.options,
            "help": f.help,
            "allow_custom": f.allow_custom,
            "operators": [{"key": op, "label": OPERATOR_LABELS[op]} for op in f.operators],
        }
        for f in FIELDS.values()
    ]
