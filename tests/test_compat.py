"""Container / codec / hardware compatibility advice (compat.py)."""

from __future__ import annotations

import pytest

from frameforge_shared.compat import AUDIO_CODECS, CONTAINERS, VIDEO_CODECS, check_spec, has_errors, options_document, required_node_features
from frameforge_shared.profile import ProfileSpec
from frameforge_shared.protocol import FEATURE_AUDIO_V2
from frameforge_shared.quality import QUALITY_TIERS, quality_tier


def fields(spec: ProfileSpec, level: str | None = None, available: dict[str, set[str]] | None = None) -> set[str]:
    return {i.field for i in check_spec(spec, available) if level is None or i.level == level}


def messages(spec: ProfileSpec, available: dict[str, set[str]] | None = None) -> str:
    return " | ".join(i.message for i in check_spec(spec, available))


def test_default_profile_has_no_errors() -> None:
    assert not has_errors(check_spec(ProfileSpec()))


@pytest.mark.parametrize("container", ["mkv", "mp4"])
@pytest.mark.parametrize("video", ["h264", "hevc", "av1", "copy"])
@pytest.mark.parametrize("audio", ["aac", "opus", "ac3", "eac3", "flac"])
def test_every_supported_combination_can_be_saved(container: str, video: str, audio: str) -> None:
    # Verified with jellyfin-ffmpeg 7.1: every one of these muxes. Advice is fine, errors are not.
    assert not has_errors(check_spec(ProfileSpec(container=container, video_codec=video, audio_codec=audio)))


def test_missing_rate_control_values_are_errors() -> None:
    assert "bitrate_kbps" in fields(ProfileSpec(rate_control="bitrate", bitrate_kbps=None), "error")
    assert "constant_quality" in fields(ProfileSpec(rate_control="constant_quality", constant_quality=None), "error")
    assert not has_errors(check_spec(ProfileSpec(rate_control="bitrate", bitrate_kbps=8000)))


def test_opus_and_flac_in_mp4_warn_with_an_aac_suggestion() -> None:
    for codec in ("opus", "flac"):
        issues = [i for i in check_spec(ProfileSpec(container="mp4", audio_codec=codec, audio_mode="transcode")) if i.field == "audio_codec" and i.level == "warn"]
        assert issues and issues[0].suggestion == {"audio_codec": "aac"}
    assert "audio_codec" not in fields(ProfileSpec(container="mkv", audio_codec="opus"), "warn")


def test_copying_opus_into_mp4_is_explained() -> None:
    issue = next(i for i in check_spec(ProfileSpec(container="mp4", audio_mode="copy_compatible")) if i.field == "audio_copy_scope")
    assert issue.suggestion == {"audio_copy_scope": "widely_playable"}
    assert "audio_copy_scope" not in fields(ProfileSpec(container="mp4", audio_copy_scope="widely_playable"))


def test_mp4_subtitle_and_attachment_limits_are_explained() -> None:
    text = messages(ProfileSpec(container="mp4"))
    assert "image subtitles" in text and "attachments" in text
    assert "image subtitles" not in messages(ProfileSpec(container="mkv"))


def test_h264_hdr_advice() -> None:
    issue = next(i for i in check_spec(ProfileSpec(video_codec="h264")) if "8-bit" in i.message)
    assert issue.suggestion == {"video_codec": "hevc"}
    assert "8-bit" not in messages(ProfileSpec(video_codec="h264", ten_bit="never"))


def test_ac3_surround_limit_and_bitrate_cap() -> None:
    spec = ProfileSpec(audio_codec="ac3", audio_bitrate_kbps=800)
    assert "5.1" in messages(spec)
    cap = next(i for i in check_spec(spec) if i.field == "audio_bitrate_kbps")
    assert cap.suggestion == {"audio_bitrate_kbps": 640}


def test_remux_ignores_scaling_limits() -> None:
    assert "ignored" in messages(ProfileSpec(video_codec="copy", max_resolution=1080))


# ---------------------------------------------------------------------------
# Hardware-aware advice
# ---------------------------------------------------------------------------


def test_no_encoder_anywhere_warns() -> None:
    issues = check_spec(ProfileSpec(video_codec="av1"), {"av1": set()})
    assert any(i.level == "warn" and "No online node" in i.message for i in issues)


def test_forced_backend_that_nobody_has() -> None:
    spec = ProfileSpec(video_codec="av1", hw_mode="nvenc", allow_cpu_fallback=False)
    issue = next(i for i in check_spec(spec, {"av1": {"cpu", "qsv"}}) if i.field == "hw_mode")
    assert issue.suggestion == {"hw_mode": "auto"}


def test_cpu_only_av1_is_flagged_as_slow() -> None:
    assert "CPU" in messages(ProfileSpec(video_codec="av1"), {"av1": {"cpu"}})
    assert "CPU" not in messages(ProfileSpec(video_codec="av1"), {"av1": {"cpu", "nvenc"}})


def test_gpu_only_without_fallback_is_fine() -> None:
    spec = ProfileSpec(video_codec="hevc", hw_mode="nvenc", allow_cpu_fallback=False)
    assert "hw_mode" not in fields(spec, available={"hevc": {"nvenc"}})


def test_without_hardware_info_there_is_no_hardware_advice() -> None:
    assert "No online node" not in messages(ProfileSpec(video_codec="av1"))


# ---------------------------------------------------------------------------
# Node features and static documents
# ---------------------------------------------------------------------------


def test_new_audio_settings_need_an_updated_node() -> None:
    assert required_node_features(ProfileSpec()) == set()
    assert required_node_features(ProfileSpec(audio_codec="opus")) == set()
    assert required_node_features(ProfileSpec(audio_codec="eac3")) == {FEATURE_AUDIO_V2}
    assert required_node_features(ProfileSpec(audio_copy_scope="widely_playable")) == {FEATURE_AUDIO_V2}


def test_options_document_covers_every_choice_the_spec_allows() -> None:
    doc = options_document()
    assert {c["value"] for c in doc["containers"]} == {"mkv", "mp4"}
    assert {v["value"] for v in doc["video_codecs"]} == {"h264", "hevc", "av1", "copy"}
    assert {a["value"] for a in doc["audio_codecs"]} == {"aac", "opus", "ac3", "eac3", "flac"}
    assert set(VIDEO_CODECS) >= set(CONTAINERS[next(iter(CONTAINERS))].video_codecs)
    assert AUDIO_CODECS["flac"].lossless and AUDIO_CODECS["flac"].default_kbps is None


@pytest.mark.parametrize(("slider", "key"), [(0, "smallest"), (29, "smallest"), (30, "compact"), (60, "balanced"), (75, "high"), (89, "high"), (90, "near_source"), (100, "near_source"), (150, "near_source")])
def test_quality_tiers(slider: int, key: str) -> None:
    assert quality_tier(slider).key == key


def test_quality_tiers_are_ordered() -> None:
    mins = [t.min_slider for t in QUALITY_TIERS]
    assert mins == sorted(mins) and mins[0] == 0
