"""Quality slider → native encoder value mapping (quality.py)."""

from __future__ import annotations

import itertools

import pytest

from frameforge_shared.codecs import Backend, VideoCodec
from frameforge_shared.quality import encoder_preset, map_quality, native_range

REAL_CODECS = [VideoCodec.H264, VideoCodec.HEVC, VideoCodec.AV1]
ALL_PAIRS = list(itertools.product(REAL_CODECS, list(Backend)))


@pytest.mark.parametrize(("codec", "backend"), ALL_PAIRS)
def test_higher_slider_never_means_worse_quality(codec: VideoCodec, backend: Backend) -> None:
    # Lower native value = better quality on every scale we map to.
    values = [map_quality(s, codec, backend).value for s in range(0, 101)]
    assert values == sorted(values, reverse=True)
    assert values[0] > values[-1], "slider must actually change the native value"


@pytest.mark.parametrize(("codec", "backend"), ALL_PAIRS)
def test_values_stay_inside_native_range(codec: VideoCodec, backend: Backend) -> None:
    lo, hi = native_range(codec, backend)
    for s in (-50, 0, 1, 50, 99, 100, 150):
        q = map_quality(s, codec, backend)
        assert lo <= q.value <= hi
        assert (q.min_value, q.max_value) == (lo, hi)


def test_slider_is_clamped() -> None:
    assert map_quality(-10, VideoCodec.HEVC, Backend.CPU).value == map_quality(0, VideoCodec.HEVC, Backend.CPU).value
    assert map_quality(500, VideoCodec.HEVC, Backend.CPU).value == map_quality(100, VideoCodec.HEVC, Backend.CPU).value


@pytest.mark.parametrize(
    ("slider", "codec", "backend", "expected"),
    [
        (70, VideoCodec.HEVC, Backend.CPU, 24),  # anchor points are hit exactly
        (50, VideoCodec.H264, Backend.CPU, 24),
        (85, VideoCodec.AV1, Backend.CPU, 27),
        (70, VideoCodec.HEVC, Backend.NVENC, 26),
        (70, VideoCodec.AV1, Backend.VAAPI, 115),
        (60, VideoCodec.HEVC, Backend.CPU, 26),  # interpolated: 27 → 24 over 50..70, halfway ≈ 25.5 → 26
    ],
)
def test_anchor_and_interpolated_values(slider: int, codec: VideoCodec, backend: Backend, expected: int) -> None:
    assert map_quality(slider, codec, backend).value == expected


def test_same_slider_means_different_native_values_per_encoder() -> None:
    # The whole point of per-encoder tables: 70 is not "CRF 24 everywhere".
    values = {(c, b): map_quality(70, c, b).value for c, b in [(VideoCodec.HEVC, Backend.CPU), (VideoCodec.AV1, Backend.CPU), (VideoCodec.AV1, Backend.VAAPI)]}
    assert len(set(values.values())) == 3


def test_av1_scales() -> None:
    assert native_range(VideoCodec.AV1, Backend.CPU) == (1, 63)  # SVT-AV1 CRF
    assert native_range(VideoCodec.AV1, Backend.VAAPI) == (0, 255)  # AV1 q-index
    assert native_range(VideoCodec.HEVC, Backend.CPU) == (0, 51)


@pytest.mark.parametrize(
    ("backend", "param"),
    [(Backend.CPU, "CRF"), (Backend.NVENC, "CQ"), (Backend.QSV, "ICQ"), (Backend.VAAPI, "QP"), (Backend.AMF, "QP")],
)
def test_label_shows_native_parameter(backend: Backend, param: str) -> None:
    q = map_quality(70, VideoCodec.HEVC, backend)
    assert q.param == param
    assert q.label == f"{param} {q.value}"


def test_override_bypasses_slider_but_is_clamped() -> None:
    assert map_quality(0, VideoCodec.HEVC, Backend.CPU, override=18).value == 18
    assert map_quality(0, VideoCodec.HEVC, Backend.CPU, override=200).value == 51
    assert map_quality(0, VideoCodec.AV1, Backend.CPU, override=0).value == 1
    assert map_quality(0, VideoCodec.AV1, Backend.VAAPI, override=200).value == 200


def test_encoder_presets() -> None:
    assert encoder_preset(VideoCodec.HEVC, Backend.CPU, "balanced") == "medium"
    assert encoder_preset(VideoCodec.AV1, Backend.CPU, "fast") == "10"  # SVT-AV1 numeric presets
    assert encoder_preset(VideoCodec.HEVC, Backend.NVENC, "max") == "p7"
    assert encoder_preset(VideoCodec.HEVC, Backend.QSV, "quality") == "slow"
    assert encoder_preset(VideoCodec.HEVC, Backend.VAAPI, "balanced") is None  # no portable knob
    assert encoder_preset(VideoCodec.HEVC, Backend.CPU, "nonsense") is None
