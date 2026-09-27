"""Normalized media description built from ffprobe output."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .codecs import codec_label

FRAMEFORGE_TAG = "FRAMEFORGE"


class VideoStream(BaseModel):
    index: int
    codec: str
    profile: str | None = None
    width: int
    height: int
    fps: float | None = None
    pix_fmt: str | None = None
    bit_depth: int = 8
    bitrate: int | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    hdr_format: str | None = None  # "HDR10", "HLG", "Dolby Vision" or None for SDR
    rotation: int = 0
    mastering_display: str | None = None  # x265 master-display string when available
    content_light: str | None = None  # "max_cll,max_fall"

    @property
    def is_hdr(self) -> bool:
        return self.hdr_format is not None

    @property
    def display_width(self) -> int:
        return self.height if abs(self.rotation) in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if abs(self.rotation) in (90, 270) else self.height

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)


class AudioStream(BaseModel):
    index: int
    codec: str
    channels: int = 2
    channel_layout: str | None = None
    sample_rate: int | None = None
    bitrate: int | None = None
    language: str | None = None
    title: str | None = None


class SubtitleStream(BaseModel):
    index: int
    codec: str
    language: str | None = None
    title: str | None = None


class MediaInfo(BaseModel):
    format_name: str
    container: str
    duration: float = 0.0
    size: int = 0
    bitrate: int | None = None
    video: VideoStream | None = None
    audio: list[AudioStream] = Field(default_factory=list)
    subtitles: list[SubtitleStream] = Field(default_factory=list)
    attachment_count: int = 0
    data_stream_count: int = 0
    chapter_count: int = 0
    tags: dict[str, str] = Field(default_factory=dict)
    creation_time: datetime | None = None

    @property
    def frameforge_tag(self) -> str | None:
        for key, value in self.tags.items():
            if key.upper() == FRAMEFORGE_TAG:
                return value
        return None

    def resolution_label(self) -> str:
        if not self.video:
            return "audio only"
        v = self.video
        short = min(v.display_width, v.display_height)
        fps = f"{round(v.fps)}" if v.fps else ""
        label = {4320: "8K", 2160: "4K"}.get(short, f"{short}p")
        if label.endswith("p") and fps:
            label += fps
        elif fps:
            label += f" {fps}fps"
        if v.display_height > v.display_width:
            label += " vertical"
        return label

    def summary(self) -> str:
        if not self.video:
            return f"{self.container} audio"
        return f"{codec_label(self.video.codec)} · {self.resolution_label()}"


# ffprobe format_name -> short container name we expose in rules/UI.
_CONTAINER_ALIASES = {
    "matroska,webm": "mkv",
    "mov,mp4,m4a,3gp,3g2,mj2": "mp4",
    "mpegts": "ts",
    "flv": "flv",
    "avi": "avi",
    "asf": "wmv",
    "mxf": "mxf",
    "mpeg": "mpeg",
}


def _container_from(format_name: str, filename: str | None) -> str:
    if format_name == "matroska,webm" and filename and filename.lower().endswith(".webm"):
        return "webm"
    if format_name == "mov,mp4,m4a,3gp,3g2,mj2" and filename and filename.lower().endswith(".mov"):
        return "mov"
    return _CONTAINER_ALIASES.get(format_name, format_name.split(",")[0])


def _parse_rate(value: str | None) -> float | None:
    if not value or value in ("0/0", "0"):
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        return float(value)
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bit_depth(stream: dict[str, Any]) -> int:
    raw = _int(stream.get("bits_per_raw_sample"))
    if raw:
        return raw
    pix = stream.get("pix_fmt") or ""
    for depth in (12, 10):
        if f"{depth}le" in pix or f"{depth}be" in pix or pix.startswith(f"p0{depth}"):
            return depth
    return 8


def _rotation(stream: dict[str, Any]) -> int:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"])))
            except (TypeError, ValueError):
                return 0
    rotate = (stream.get("tags") or {}).get("rotate")
    return _int(rotate) or 0


def _ratio_to_int(value: str | None, scale: int) -> int | None:
    parsed = _parse_rate(value)
    return int(round(parsed * scale)) if parsed is not None else None


def _hdr_side_data(stream: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    master: str | None = None
    light: str | None = None
    dovi = False
    for sd in stream.get("side_data_list") or []:
        kind = sd.get("side_data_type", "")
        if kind == "Mastering display metadata":
            try:
                g = (_ratio_to_int(sd.get("green_x"), 50000), _ratio_to_int(sd.get("green_y"), 50000))
                b = (_ratio_to_int(sd.get("blue_x"), 50000), _ratio_to_int(sd.get("blue_y"), 50000))
                r = (_ratio_to_int(sd.get("red_x"), 50000), _ratio_to_int(sd.get("red_y"), 50000))
                wp = (_ratio_to_int(sd.get("white_point_x"), 50000), _ratio_to_int(sd.get("white_point_y"), 50000))
                lum = (_ratio_to_int(sd.get("max_luminance"), 10000), _ratio_to_int(sd.get("min_luminance"), 10000))
                if None not in (*g, *b, *r, *wp, *lum):
                    master = f"G({g[0]},{g[1]})B({b[0]},{b[1]})R({r[0]},{r[1]})WP({wp[0]},{wp[1]})L({lum[0]},{lum[1]})"
            except (TypeError, ValueError):
                master = None
        elif kind == "Content light level metadata":
            light = f"{sd.get('max_content', 0)},{sd.get('max_average', 0)}"
        elif "DOVI" in kind or "Dolby Vision" in kind:
            dovi = True
    return master, light, dovi


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_ffprobe(data: dict[str, Any], filename: str | None = None) -> MediaInfo:
    fmt = data.get("format") or {}
    format_name = fmt.get("format_name", "unknown")
    streams = data.get("streams") or []
    tags = {str(k): str(v) for k, v in (fmt.get("tags") or {}).items()}

    video: VideoStream | None = None
    audio: list[AudioStream] = []
    subs: list[SubtitleStream] = []
    attachments = 0
    data_streams = 0

    for s in streams:
        ctype = s.get("codec_type")
        stags = s.get("tags") or {}
        if ctype == "video":
            if (s.get("disposition") or {}).get("attached_pic"):
                attachments += 1
                continue
            if video is not None:
                continue
            master, light, dovi = _hdr_side_data(s)
            trc = s.get("color_transfer")
            hdr = None
            if dovi:
                hdr = "Dolby Vision"
            elif trc == "smpte2084":
                hdr = "HDR10"
            elif trc == "arib-std-b67":
                hdr = "HLG"
            video = VideoStream(
                index=int(s["index"]),
                codec=s.get("codec_name", "unknown"),
                profile=s.get("profile"),
                width=_int(s.get("width")) or 0,
                height=_int(s.get("height")) or 0,
                fps=_parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate")),
                pix_fmt=s.get("pix_fmt"),
                bit_depth=_bit_depth(s),
                bitrate=_int(s.get("bit_rate")),
                color_primaries=s.get("color_primaries"),
                color_transfer=trc,
                color_space=s.get("color_space"),
                color_range=s.get("color_range"),
                hdr_format=hdr,
                rotation=_rotation(s),
                mastering_display=master,
                content_light=light,
            )
        elif ctype == "audio":
            audio.append(
                AudioStream(
                    index=int(s["index"]),
                    codec=s.get("codec_name", "unknown"),
                    channels=_int(s.get("channels")) or 2,
                    channel_layout=s.get("channel_layout"),
                    sample_rate=_int(s.get("sample_rate")),
                    bitrate=_int(s.get("bit_rate")),
                    language=stags.get("language"),
                    title=stags.get("title"),
                )
            )
        elif ctype == "subtitle":
            subs.append(
                SubtitleStream(
                    index=int(s["index"]),
                    codec=s.get("codec_name", "unknown"),
                    language=stags.get("language"),
                    title=stags.get("title"),
                )
            )
        elif ctype == "attachment":
            attachments += 1
        else:
            data_streams += 1

    duration = float(fmt.get("duration") or 0.0)
    if not duration and video is not None:
        for s in streams:
            if s.get("index") == video.index and s.get("duration"):
                duration = float(s["duration"])

    return MediaInfo(
        format_name=format_name,
        container=_container_from(format_name, filename),
        duration=duration,
        size=_int(fmt.get("size")) or 0,
        bitrate=_int(fmt.get("bit_rate")),
        video=video,
        audio=audio,
        subtitles=subs,
        attachment_count=attachments,
        data_stream_count=data_streams,
        chapter_count=len(data.get("chapters") or []),
        tags=tags,
        creation_time=_parse_datetime(tags.get("creation_time")),
    )
