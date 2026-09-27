"""What containers, video codecs and audio codecs can do together, in plain language.

``check_spec`` turns a profile into a list of issues for the editor. Errors block saving a
profile; warnings and infos are advice. It runs at the API boundary only: stored profiles and
job snapshots are never rejected on load, so a stricter matrix can't strand existing work.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel

from .codecs import AUDIO_COPY_COMPAT, AUDIO_COPY_WIDELY_PLAYABLE, AUDIO_LABELS, AUDIO_MAX_CHANNELS, AUDIO_MAX_KBPS, BACKEND_LABELS, Backend, Container, codec_label
from .profile import ProfileSpec
from .protocol import FEATURE_AUDIO_V2

IssueLevel = Literal["error", "warn", "info"]


class Issue(BaseModel):
    level: IssueLevel
    field: str  # ProfileSpec field the issue is about
    message: str
    suggestion: dict[str, Any] | None = None  # spec changes that resolve it
    suggestion_label: str | None = None


class ContainerInfo(BaseModel):
    value: str
    label: str
    description: str
    video_codecs: list[str]
    audio_codecs: list[str]  # codecs a profile can convert audio to
    audio_copy: list[str]  # source audio it can store as-is
    audio_playable: list[str]  # source audio most players handle in it
    subtitles: str
    attachments: bool


class VideoCodecInfo(BaseModel):
    value: str
    label: str
    description: str
    efficiency: str  # rough size relative to H.264 at similar quality
    keeps_hdr: bool


class AudioCodecInfo(BaseModel):
    value: str
    label: str
    description: str
    default_kbps: int | None  # per stereo pair; None for lossless
    max_kbps: int | None
    max_channels: int | None
    lossless: bool


CONTAINERS: dict[Container, ContainerInfo] = {
    Container.MP4: ContainerInfo(
        value="mp4",
        label="MP4",
        description="Plays almost everywhere: editors, phones, browsers, smart TVs and Plex direct play.",
        video_codecs=["h264", "hevc", "av1", "copy"],
        audio_codecs=["aac", "opus", "ac3", "eac3", "flac"],
        audio_copy=sorted(AUDIO_COPY_COMPAT[Container.MP4]),
        audio_playable=sorted(AUDIO_COPY_WIDELY_PLAYABLE[Container.MP4]),
        subtitles="Text subtitles are converted to MP4 text; image subtitles (PGS, DVD) are dropped.",
        attachments=False,
    ),
    Container.MKV: ContainerInfo(
        value="mkv",
        label="MKV",
        description="Holds every audio, subtitle and attachment type. Best for archives; some editors and TVs handle it poorly.",
        video_codecs=["h264", "hevc", "av1", "copy"],
        audio_codecs=["aac", "opus", "ac3", "eac3", "flac"],
        audio_copy=sorted(AUDIO_COPY_COMPAT[Container.MKV]),
        audio_playable=sorted(AUDIO_COPY_WIDELY_PLAYABLE[Container.MKV]),
        subtitles="Keeps every subtitle track, text and image.",
        attachments=True,
    ),
}

VIDEO_CODECS: dict[str, VideoCodecInfo] = {
    "h264": VideoCodecInfo(value="h264", label="H.264", description="Plays on everything, including old TVs and browsers. Largest files.", efficiency="baseline", keeps_hdr=False),
    "hevc": VideoCodecInfo(
        value="hevc", label="H.265", description="About half the size of H.264 at similar quality. Plays on most devices from the last ~8 years.", efficiency="≈50–60% of H.264", keeps_hdr=True
    ),
    "av1": VideoCodecInfo(
        value="av1",
        label="AV1",
        description="Smallest files. Needs recent devices to play, and a recent GPU (or a lot of CPU time) to encode.",
        efficiency="≈35–45% of H.264",
        keeps_hdr=True,
    ),
    "copy": VideoCodecInfo(value="copy", label="Copy (remux)", description="Keeps the video exactly as it is and only changes the container. Fast and lossless.", efficiency="unchanged", keeps_hdr=True),
}

AUDIO_CODECS: dict[str, AudioCodecInfo] = {
    "aac": AudioCodecInfo(value="aac", label="AAC", description="Plays everywhere. The safe choice.", default_kbps=160, max_kbps=AUDIO_MAX_KBPS["aac"], max_channels=None, lossless=False),
    "opus": AudioCodecInfo(
        value="opus",
        label="Opus",
        description="Best quality per bit. Plays in browsers, Plex and Android; not in Premiere, Final Cut or older Apple devices.",
        default_kbps=128,
        max_kbps=AUDIO_MAX_KBPS["opus"],
        max_channels=None,
        lossless=False,
    ),
    "ac3": AudioCodecInfo(
        value="ac3", label="AC-3 (Dolby Digital)", description="Surround that home theater receivers decode. Up to 5.1.", default_kbps=192, max_kbps=AUDIO_MAX_KBPS["ac3"], max_channels=6, lossless=False
    ),
    "eac3": AudioCodecInfo(
        value="eac3", label="E-AC-3 (Dolby Digital Plus)", description="More efficient AC-3 for TVs and streaming boxes. Up to 5.1 here.", default_kbps=160, max_kbps=AUDIO_MAX_KBPS["eac3"], max_channels=6, lossless=False
    ),
    "flac": AudioCodecInfo(value="flac", label="FLAC", description="Lossless. Large audio, but nothing is lost. Limited editor support in MP4.", default_kbps=None, max_kbps=None, max_channels=None, lossless=True),
}

# Where a codec's audio can be picky about the player, per container.
_AUDIO_PLAYBACK_WARNINGS: dict[tuple[Container, str], str] = {
    (Container.MP4, "opus"): "Opus in MP4 plays in browsers, Plex and Android, but not in Premiere, Final Cut or older Apple devices.",
    (Container.MP4, "flac"): "FLAC in MP4 isn't supported by most editors and Apple devices. Use MKV for lossless archives, or AAC for compatibility.",
}


def _available_backends(available: Mapping[str, set[str]] | None, codec: str) -> set[str] | None:
    if available is None:
        return None
    return set(available.get(codec, set()))


def check_spec(spec: ProfileSpec, available: Mapping[str, set[str]] | None = None) -> list[Issue]:
    """Explain what a profile will and won't do. ``available`` maps video codec → verified backends on online nodes."""
    issues: list[Issue] = []
    container = Container(spec.container)
    codec = spec.video_codec.value
    remux = spec.is_remux

    # --- Rate control needs its value ----------------------------------------
    if not remux and spec.rate_control == "bitrate" and not spec.bitrate_kbps:
        issues.append(Issue(level="error", field="bitrate_kbps", message="Enter a target bitrate, or switch rate control back to the quality slider."))
    if not remux and spec.rate_control == "constant_quality" and spec.constant_quality is None:
        issues.append(Issue(level="error", field="constant_quality", message="Enter the exact CRF / CQ / QP value, or switch rate control back to the quality slider."))

    # --- Video ------------------------------------------------------------------
    if codec not in CONTAINERS[container].video_codecs:
        issues.append(Issue(level="error", field="video_codec", message=f"{codec_label(codec)} can't be stored in {container.value.upper()}.", suggestion={"container": "mkv"}, suggestion_label="Use MKV"))
    if remux and (spec.max_resolution or spec.max_fps):
        issues.append(Issue(level="info", field="max_resolution", message="Resolution and frame rate limits are ignored when the video is copied."))
    if codec == "h264" and spec.ten_bit == "auto":
        issues.append(
            Issue(
                level="info",
                field="video_codec",
                message="H.264 output is always 8-bit. HDR recordings will look washed out; H.265 and AV1 keep HDR.",
                suggestion={"video_codec": "hevc"},
                suggestion_label="Use H.265",
            )
        )
    if codec == "av1":
        issues.append(Issue(level="info", field="video_codec", message="AV1 needs a recent TV, phone or browser to play. Older Plex clients will make the server convert it on the fly."))

    backends = _available_backends(available, codec) if not remux else None
    if backends is not None:
        label = codec_label(codec)
        forced = spec.hw_mode not in ("auto", "cpu")
        hw = backends - {Backend.CPU.value}
        if not backends:
            issues.append(Issue(level="warn", field="video_codec", message=f"No online node can encode {label} right now. Jobs will wait until one can."))
        elif forced and spec.hw_mode not in backends and not (spec.allow_cpu_fallback and Backend.CPU.value in backends):
            name = BACKEND_LABELS[Backend(spec.hw_mode)]
            issues.append(
                Issue(
                    level="warn",
                    field="hw_mode",
                    message=f"No online node has a working {name} {label} encoder. Jobs will wait.",
                    suggestion={"hw_mode": "auto"},
                    suggestion_label="Use any available hardware",
                )
            )
        elif spec.hw_mode == "cpu" or not hw:
            if spec.hw_mode != "cpu" and not spec.allow_cpu_fallback:
                issues.append(Issue(level="warn", field="hw_mode", message=f"No online node has a GPU {label} encoder and CPU fallback is off. Jobs will wait."))
            elif codec in ("av1", "hevc"):
                issues.append(Issue(level="info", field="video_codec", message=f"{label} will be encoded on the CPU. Expect it to run slower than real time for 4K footage."))

    # --- Audio ------------------------------------------------------------------
    audio = spec.audio_codec
    audio_label = AUDIO_LABELS.get(audio, audio.upper())
    if audio not in CONTAINERS[container].audio_codecs:
        issues.append(Issue(level="error", field="audio_codec", message=f"{audio_label} can't be stored in {container.value.upper()}.", suggestion={"audio_codec": "aac"}, suggestion_label="Use AAC"))
    playback = _AUDIO_PLAYBACK_WARNINGS.get((container, audio))
    if playback:
        issues.append(Issue(level="warn", field="audio_codec", message=playback, suggestion={"audio_codec": "aac"}, suggestion_label="Use AAC"))
    if audio in AUDIO_MAX_CHANNELS:
        issues.append(Issue(level="info", field="audio_codec", message=f"{audio_label} holds at most 5.1. 7.1 tracks are downmixed."))
    max_kbps = AUDIO_MAX_KBPS.get(audio)
    if max_kbps and spec.audio_bitrate_kbps > max_kbps:
        issues.append(Issue(level="warn", field="audio_bitrate_kbps", message=f"{audio_label} is capped at {max_kbps} kb/s per track.", suggestion={"audio_bitrate_kbps": max_kbps}, suggestion_label=f"Use {max_kbps} kb/s"))
    if spec.audio_mode != "transcode" and container == Container.MP4 and spec.audio_copy_scope == "storable":
        issues.append(
            Issue(
                level="info",
                field="audio_copy_scope",
                message="Opus and FLAC tracks are copied into MP4 as-is. Some editors and Apple devices can't play them.",
                suggestion={"audio_copy_scope": "widely_playable"},
                suggestion_label="Convert them instead",
            )
        )

    # --- Subtitles / attachments --------------------------------------------------
    if container == Container.MP4 and spec.keep_subtitles:
        issues.append(Issue(level="info", field="container", message="MP4 converts text subtitles and drops image subtitles (PGS, DVD). Dropped tracks are listed on the job."))
    if container == Container.MP4 and spec.keep_attachments:
        issues.append(Issue(level="info", field="container", message="MP4 can't hold attachments (fonts, cover art). They are dropped and listed on the job."))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)


def required_node_features(spec: ProfileSpec) -> set[str]:
    """Node features a job with this profile needs. Older nodes would misread these settings."""
    needed: set[str] = set()
    if spec.audio_codec not in ("aac", "opus") or spec.audio_copy_scope != "storable":
        needed.add(FEATURE_AUDIO_V2)
    return needed


def options_document() -> dict[str, Any]:
    """Static format knowledge for the profile editor."""
    return {
        "containers": [c.model_dump() for c in CONTAINERS.values()],
        "video_codecs": [v.model_dump() for v in VIDEO_CODECS.values()],
        "audio_codecs": [a.model_dump() for a in AUDIO_CODECS.values()],
    }
