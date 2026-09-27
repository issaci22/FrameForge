"""Encoder choice per node (encoders.py): only verified encoders, preference order, HW decode fallback."""

from __future__ import annotations

from frameforge_shared import protocol as P
from frameforge_shared.encoders import select_encoder
from frameforge_shared.profile import ProfileSpec

from .conftest import ALL_CPU, make_caps, make_media, make_video

NVENC = ("hevc_nvenc", "hevc", "nvenc")
QSV = ("hevc_qsv", "hevc", "qsv")
VAAPI = ("hevc_vaapi", "hevc", "vaapi")


def test_auto_prefers_nvenc_then_qsv_then_vaapi() -> None:
    assert select_encoder(ProfileSpec(), make_caps([*ALL_CPU, VAAPI, QSV, NVENC])).choice.encoder == "hevc_nvenc"
    assert select_encoder(ProfileSpec(), make_caps([*ALL_CPU, VAAPI, QSV])).choice.encoder == "hevc_qsv"
    assert select_encoder(ProfileSpec(), make_caps([*ALL_CPU, VAAPI])).choice.encoder == "hevc_vaapi"
    assert select_encoder(ProfileSpec(), make_caps(ALL_CPU)).choice.encoder == "libx265"


def test_unverified_encoder_is_never_chosen() -> None:
    caps = make_caps(ALL_CPU)
    caps.encoders.append(caps.encoders[0].model_copy(update={"name": "hevc_nvenc", "backend": "nvenc", "codec": "hevc", "verified": False}))
    assert select_encoder(ProfileSpec(), caps).choice.encoder == "libx265"


def test_forced_backend_without_fallback_explains_why() -> None:
    sel = select_encoder(ProfileSpec(hw_mode="nvenc", allow_cpu_fallback=False), make_caps(ALL_CPU))
    assert sel.choice is None
    assert "CPU fallback disabled" in (sel.reason or "")


def test_forced_backend_falls_back_to_cpu_when_allowed() -> None:
    assert select_encoder(ProfileSpec(hw_mode="qsv"), make_caps(ALL_CPU)).choice.backend == "cpu"


def test_cpu_mode_ignores_gpus() -> None:
    assert select_encoder(ProfileSpec(hw_mode="cpu"), make_caps([*ALL_CPU, NVENC])).choice.backend == "cpu"


def test_no_ffmpeg_and_handbrake_are_reported() -> None:
    caps = make_caps(ALL_CPU)
    caps.engines["ffmpeg"] = False
    assert "FFmpeg is not available" in (select_encoder(ProfileSpec(), caps).reason or "")
    assert "not available" in (select_encoder(ProfileSpec(engine="handbrake"), make_caps(ALL_CPU)).reason or "")


def test_hw_decode_only_when_decoder_verified() -> None:
    media = make_media(make_video(codec="h264"))
    with_dec = make_caps([VAAPI], decoders=[("vaapi", "h264")], render_device="/dev/dri/renderD129")
    sel = select_encoder(ProfileSpec(), with_dec, media).choice
    assert sel.hw_decode is True
    assert sel.device == "/dev/dri/renderD129"
    assert select_encoder(ProfileSpec(), make_caps([VAAPI]), media).choice.hw_decode is False


def test_qsv_uses_vaapi_decoders() -> None:
    media = make_media(make_video(codec="hevc"))
    sel = select_encoder(ProfileSpec(), make_caps([QSV], decoders=[("vaapi", "hevc")]), media).choice
    assert sel.backend == "qsv" and sel.hw_decode is True


def test_hw_decode_disabled_for_rotated_source() -> None:
    media = make_media(make_video(rotation=90))
    assert select_encoder(ProfileSpec(), make_caps([NVENC], decoders=[("nvenc", "h264")]), media).choice.hw_decode is False


def test_hw_decode_disabled_when_profile_says_so() -> None:
    media = make_media()
    assert select_encoder(ProfileSpec(hw_decode=False), make_caps([NVENC], decoders=[("nvenc", "h264")]), media).choice.hw_decode is False


def test_remux_needs_no_encoder() -> None:
    sel = select_encoder(ProfileSpec(video_codec="copy"), make_caps([]))
    assert sel.choice.encoder == "copy"


def test_node_without_the_audio_encoder_is_not_chosen() -> None:
    caps = make_caps(ALL_CPU)
    caps.audio_encoders = [P.AudioEncoderCapability(name="aac", codec="aac", verified=True), P.AudioEncoderCapability(name="eac3", codec="eac3", verified=False, error="missing")]
    sel = select_encoder(ProfileSpec(audio_codec="eac3"), caps)
    assert sel.choice is None and "E-AC-3" in (sel.reason or "")
    assert select_encoder(ProfileSpec(audio_codec="aac"), caps).choice is not None


def test_nodes_from_before_audio_detection_can_do_aac_and_opus_only() -> None:
    caps = make_caps(ALL_CPU)  # audio_encoders is None
    assert select_encoder(ProfileSpec(audio_codec="opus"), caps).choice is not None
    assert select_encoder(ProfileSpec(audio_codec="flac"), caps).choice is None
