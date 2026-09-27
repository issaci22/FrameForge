"""Map the friendly 0–100 quality slider onto each encoder's native rate-control scale.

The slider does NOT mean the same thing across codecs. Each encoder family gets its own
anchor table, calibrated so that equal slider values give *roughly comparable* perceived
quality for typical gameplay/camera footage. The UI always shows the native value.
"""

from __future__ import annotations

from dataclasses import dataclass

from .codecs import Backend, VideoCodec

# (slider, native value) anchor points, interpolated linearly. Lower native value = better.
_ANCHORS: dict[tuple[VideoCodec, Backend], list[tuple[int, float]]] = {
    (VideoCodec.H264, Backend.CPU): [(0, 34), (50, 24), (70, 21), (85, 19), (100, 15)],
    (VideoCodec.HEVC, Backend.CPU): [(0, 36), (50, 27), (70, 24), (85, 21), (100, 16)],
    (VideoCodec.AV1, Backend.CPU): [(0, 52), (50, 38), (70, 32), (85, 27), (100, 18)],
    (VideoCodec.H264, Backend.NVENC): [(0, 36), (50, 27), (70, 24), (85, 21), (100, 16)],
    (VideoCodec.HEVC, Backend.NVENC): [(0, 38), (50, 29), (70, 26), (85, 23), (100, 18)],
    (VideoCodec.AV1, Backend.NVENC): [(0, 44), (50, 34), (70, 30), (85, 26), (100, 20)],
    (VideoCodec.H264, Backend.QSV): [(0, 34), (50, 26), (70, 23), (85, 21), (100, 17)],
    (VideoCodec.HEVC, Backend.QSV): [(0, 36), (50, 27), (70, 24), (85, 22), (100, 18)],
    (VideoCodec.AV1, Backend.QSV): [(0, 42), (50, 32), (70, 28), (85, 25), (100, 20)],
    (VideoCodec.H264, Backend.VAAPI): [(0, 34), (50, 26), (70, 23), (85, 21), (100, 17)],
    (VideoCodec.HEVC, Backend.VAAPI): [(0, 36), (50, 28), (70, 25), (85, 22), (100, 18)],
    # AV1 VA-API CQP uses the AV1 q-index scale (0–255).
    (VideoCodec.AV1, Backend.VAAPI): [(0, 200), (50, 140), (70, 115), (85, 95), (100, 70)],
    (VideoCodec.H264, Backend.AMF): [(0, 34), (50, 26), (70, 23), (85, 21), (100, 17)],
    (VideoCodec.HEVC, Backend.AMF): [(0, 36), (50, 28), (70, 25), (85, 22), (100, 18)],
    (VideoCodec.AV1, Backend.AMF): [(0, 200), (50, 140), (70, 115), (85, 95), (100, 70)],
}

_RANGES: dict[tuple[VideoCodec, Backend], tuple[int, int]] = {
    (VideoCodec.AV1, Backend.CPU): (1, 63),
    (VideoCodec.AV1, Backend.VAAPI): (0, 255),
    (VideoCodec.AV1, Backend.AMF): (0, 255),
}
_DEFAULT_RANGE = (0, 51)

_PARAM_NAMES: dict[Backend, str] = {
    Backend.CPU: "CRF",
    Backend.NVENC: "CQ",
    Backend.QSV: "ICQ",
    Backend.VAAPI: "QP",
    Backend.AMF: "QP",
}


@dataclass(frozen=True)
class QualityValue:
    codec: VideoCodec
    backend: Backend
    value: int
    param: str  # human name, e.g. "CRF"
    min_value: int
    max_value: int

    @property
    def label(self) -> str:
        return f"{self.param} {self.value}"


def native_range(codec: VideoCodec, backend: Backend) -> tuple[int, int]:
    return _RANGES.get((codec, backend), _DEFAULT_RANGE)


def map_quality(slider: int, codec: VideoCodec, backend: Backend, override: int | None = None) -> QualityValue:
    """Translate a 0–100 slider (or a raw override) into the encoder's native value."""
    lo, hi = native_range(codec, backend)
    if override is not None:
        value = max(lo, min(hi, int(override)))
    else:
        anchors = _ANCHORS.get((codec, backend)) or _ANCHORS[(codec, Backend.CPU)]
        s = max(0, min(100, slider))
        value_f = anchors[-1][1]
        for (x0, y0), (x1, y1) in zip(anchors, anchors[1:]):
            if x0 <= s <= x1:
                t = (s - x0) / (x1 - x0) if x1 != x0 else 0.0
                value_f = y0 + t * (y1 - y0)
                break
        value = max(lo, min(hi, int(round(value_f))))
    return QualityValue(codec, backend, value, _PARAM_NAMES[backend], lo, hi)


@dataclass(frozen=True)
class QualityTier:
    key: str
    label: str
    description: str
    min_slider: int


# Plain-language bands for the slider. They describe intent, not a promised file size.
QUALITY_TIERS: tuple[QualityTier, ...] = (
    QualityTier("smallest", "Smallest", "Visible softening and banding in detailed or dark scenes. For footage you only need to keep.", 0),
    QualityTier("compact", "Compact", "Fine detail and film grain soften; fine for talking heads and screen recordings.", 30),
    QualityTier("balanced", "Balanced", "Hard to tell from the source at normal viewing distance for most recordings.", 55),
    QualityTier("high", "High", "Keeps fine detail and grain. Good for footage you may re-edit.", 75),
    QualityTier("near_source", "Near source", "Very close to the original. Files shrink much less.", 90),
)


def quality_tier(slider: int) -> QualityTier:
    s = max(0, min(100, slider))
    tier = QUALITY_TIERS[0]
    for t in QUALITY_TIERS:
        if s >= t.min_slider:
            tier = t
    return tier


# Encoder speed presets for the friendly "speed" setting.
_PRESETS: dict[Backend, dict[str, str]] = {
    Backend.NVENC: {"fast": "p3", "balanced": "p5", "quality": "p6", "max": "p7"},
    Backend.QSV: {"fast": "veryfast", "balanced": "medium", "quality": "slow", "max": "veryslow"},
    Backend.AMF: {"fast": "speed", "balanced": "balanced", "quality": "quality", "max": "quality"},
}
_CPU_PRESETS: dict[VideoCodec, dict[str, str]] = {
    VideoCodec.H264: {"fast": "veryfast", "balanced": "medium", "quality": "slow", "max": "slower"},
    VideoCodec.HEVC: {"fast": "veryfast", "balanced": "medium", "quality": "slow", "max": "slower"},
    VideoCodec.AV1: {"fast": "10", "balanced": "8", "quality": "6", "max": "4"},
}


def encoder_preset(codec: VideoCodec, backend: Backend, speed: str) -> str | None:
    if backend == Backend.CPU:
        return _CPU_PRESETS[codec].get(speed)
    if backend == Backend.VAAPI:
        return None  # VA-API has no portable preset knob
    return _PRESETS[backend].get(speed)
