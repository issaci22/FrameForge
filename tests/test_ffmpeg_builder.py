"""FFmpeg argument construction (ffmpeg_builder.py) and encoder selection (encoders.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from frameforge_shared.ffmpeg_builder import BuildError, build_command, build_preview_commands
from frameforge_shared.media import AudioStream
from frameforge_shared.probe import probe_file
from frameforge_shared.profile import ProfileSpec
from frameforge_shared.protocol import EncoderChoice

from .conftest import make_clip, make_media, make_video, needs_ffmpeg, sub

CPU_HEVC = EncoderChoice(codec="hevc", backend="cpu", encoder="libx265")
OUT = "/media/vods/.frameforge-tmp/job-1.mkv"


def build(media=None, spec=None, choice=CPU_HEVC, **kw):  # noqa: ANN001, ANN003, ANN201
    return build_command(media or make_media(), spec or ProfileSpec(), choice, "/media/vods/in.mkv", OUT, "job=1;profile=2;v=1", **kw)


def opt(args: list[str], name: str) -> str | None:
    """Value following the first occurrence of ``name``."""
    return args[args.index(name) + 1] if name in args else None


def vf(args: list[str]) -> list[str]:
    value = opt(args, "-vf")
    return value.split(",") if value else []


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


def test_is_an_argument_list_with_progress_and_single_output() -> None:
    r = build()
    assert isinstance(r.args, list) and all(isinstance(a, str) for a in r.args)
    assert opt(r.args, "-progress") == "pipe:1"
    assert "-nostdin" in r.args
    assert r.args[-1] == OUT
    assert r.args.count(OUT) == 1
    assert opt(r.args, "-i") == "/media/vods/in.mkv"


def test_frameforge_tag_is_written() -> None:
    r = build()
    assert "FRAMEFORGE=job=1;profile=2;v=1" in r.args


def test_no_video_stream_is_a_build_error() -> None:
    media = make_media()
    media.video = None
    with pytest.raises(BuildError):
        build(media)


def test_quality_label_is_native() -> None:
    r = build(spec=ProfileSpec(quality=70))
    assert r.quality_label == "CRF 24"
    assert opt(r.args, "-crf") == "24"


def test_bitrate_mode() -> None:
    r = build(spec=ProfileSpec(rate_control="bitrate", bitrate_kbps=6000))
    assert opt(r.args, "-b:v") == "6000k"
    assert opt(r.args, "-maxrate") == "9000k"
    assert "-crf" not in r.args
    assert r.quality_label == "6000 kb/s"


def test_constant_quality_override() -> None:
    r = build(spec=ProfileSpec(rate_control="constant_quality", constant_quality=19))
    assert opt(r.args, "-crf") == "19"


# ---------------------------------------------------------------------------
# Resolution / frame rate caps: never upscale, cap the SHORT side
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("w", "h", "rotation", "cap", "expected"),
    [
        (3840, 2160, 0, 1080, (1920, 1080)),
        (2560, 1440, 0, 1080, (1920, 1080)),
        (1080, 1920, 0, 720, (720, 1280)),  # vertical: short side is the width
        (1920, 1080, 90, 720, (720, 1280)),  # rotated phone footage: capped in display orientation
        (1920, 1080, 0, 1080, None),  # already at the cap
        (1280, 720, 0, 1080, None),  # never upscale
        (1080, 1920, 0, 1080, None),  # vertical 1080p isn't "1920p"
    ],
)
def test_resolution_cap(w: int, h: int, rotation: int, cap: int, expected: tuple[int, int] | None) -> None:
    r = build(make_media(make_video(width=w, height=h, rotation=rotation)), ProfileSpec(max_resolution=cap))
    got = (r.target_width, r.target_height) if r.target_width else None
    assert got == expected
    scale = [f for f in vf(r.args) if f.startswith("scale=")]
    if expected:
        assert scale == [f"scale={expected[0]}:{expected[1]}:flags=lanczos"]
    else:
        assert scale == []


def test_scaled_dimensions_are_even() -> None:
    r = build(make_media(make_video(width=1366, height=768)), ProfileSpec(max_resolution=720))
    assert r.target_width is not None and r.target_height is not None
    assert r.target_width % 2 == 0 and r.target_height % 2 == 0
    assert r.target_height == 720


@pytest.mark.parametrize(("fps", "cap", "limited"), [(60.0, 30, True), (30.0, 60, False), (30.4, 30, False), (59.94, 60, False)])
def test_fps_cap_never_raises_frame_rate(fps: float, cap: float, limited: bool) -> None:
    r = build(make_media(make_video(fps=fps)), ProfileSpec(max_fps=cap))
    assert ("-fpsmax" in r.args) is limited
    assert "-r" not in r.args  # never force a rate (that would duplicate frames)


# ---------------------------------------------------------------------------
# Streams and metadata preservation
# ---------------------------------------------------------------------------


def test_metadata_and_chapters_kept_by_default() -> None:
    r = build()
    assert opt(r.args, "-map_metadata") == "0"
    assert opt(r.args, "-map_chapters") == "0"


def test_metadata_and_chapters_can_be_dropped() -> None:
    r = build(spec=ProfileSpec(keep_metadata=False, keep_chapters=False))
    assert opt(r.args, "-map_metadata") == "-1"
    assert opt(r.args, "-map_chapters") == "-1"


def test_every_audio_track_is_mapped() -> None:
    media = make_media(audio=[AudioStream(index=1, codec="aac"), AudioStream(index=2, codec="opus"), AudioStream(index=3, codec="pcm_s16le")])
    r = build(media)
    maps = [r.args[i + 1] for i, a in enumerate(r.args) if a == "-map"]
    assert {"0:1", "0:2", "0:3"} <= set(maps)


def test_copy_compatible_audio_copies_what_fits_and_converts_the_rest() -> None:
    media = make_media(audio=[AudioStream(index=1, codec="aac"), AudioStream(index=2, codec="pcm_s16le", channels=2)])
    r = build(media, ProfileSpec(container="mp4"), EncoderChoice(codec="hevc", backend="cpu", encoder="libx265"))
    assert opt(r.args, "-c:a:0") == "copy"
    assert opt(r.args, "-c:a:1") == "aac"  # PCM can't live in MP4


def test_audio_copy_mode_records_a_note_when_forced_to_convert() -> None:
    media = make_media(audio=[AudioStream(index=1, codec="pcm_s24le")])
    r = build(media, ProfileSpec(container="mp4", audio_mode="copy"))
    assert opt(r.args, "-c:a:0") == "aac"
    assert any("can't be stored in MP4" in n for n in r.notes)


def test_surround_opus_bitrate_scales_and_uses_mapping_family() -> None:
    media = make_media(audio=[AudioStream(index=1, codec="dts", channels=6)])
    r = build(media, ProfileSpec(audio_mode="transcode", audio_codec="opus", audio_bitrate_kbps=128))
    assert opt(r.args, "-c:a:0") == "libopus"
    assert opt(r.args, "-b:a:0") == "384k"
    assert opt(r.args, "-mapping_family:a:0") == "1"


def test_mkv_keeps_all_subtitles() -> None:
    media = make_media(subtitles=[sub(2, "subrip"), sub(3, "hdmv_pgs_subtitle")])
    r = build(media)
    assert opt(r.args, "-c:s:0") == "copy"
    assert opt(r.args, "-c:s:1") == "copy"
    assert not any("subtitle" in n for n in r.notes)


def test_mp4_converts_text_subs_and_drops_bitmap_subs_with_a_note() -> None:
    media = make_media(subtitles=[sub(2, "subrip"), sub(3, "hdmv_pgs_subtitle")])
    r = build(media, ProfileSpec(container="mp4"))
    assert opt(r.args, "-c:s:0") == "mov_text"
    assert "-c:s:1" not in r.args
    assert "0:3" not in r.args
    assert any("image-based subtitle" in n for n in r.notes)


def test_subtitles_removed_by_profile_are_noted() -> None:
    r = build(make_media(subtitles=[sub(2, "ass")]), ProfileSpec(keep_subtitles=False))
    assert "-c:s:0" not in r.args
    assert any("Removed 1 subtitle" in n for n in r.notes)


def test_attachments_kept_mkv_to_mkv() -> None:
    r = build(make_media(attachment_count=2))
    assert "0:t?" in r.args


def test_attachments_dropped_for_mp4_with_note() -> None:
    r = build(make_media(attachment_count=1), ProfileSpec(container="mp4"))
    assert "0:t?" not in r.args
    assert any("attachment" in n for n in r.notes)


def test_data_streams_are_noted() -> None:
    r = build(make_media(data_stream_count=1))
    assert any("data stream" in n for n in r.notes)


def test_mp4_flags() -> None:
    r = build(spec=ProfileSpec(container="mp4"))
    assert opt(r.args, "-movflags") == "+use_metadata_tags+faststart"
    assert opt(r.args, "-tag:v") == "hvc1"
    assert opt(r.args, "-f") == "mp4"


# ---------------------------------------------------------------------------
# Color / HDR
# ---------------------------------------------------------------------------

HDR10 = {
    "codec": "hevc",
    "width": 3840,
    "height": 2160,
    "bit_depth": 10,
    "pix_fmt": "yuv420p10le",
    "color_primaries": "bt2020",
    "color_transfer": "smpte2084",
    "color_space": "bt2020nc",
    "color_range": "tv",
    "hdr_format": "HDR10",
    "mastering_display": "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)",
    "content_light": "1000,400",
}


def test_hdr10_kept_as_10_bit_hevc_with_metadata() -> None:
    r = build(make_media(make_video(**HDR10)))
    assert "format=yuv420p10le" in vf(r.args)
    assert opt(r.args, "-color_trc") == "smpte2084"
    assert opt(r.args, "-color_primaries") == "bt2020"
    x265 = opt(r.args, "-x265-params") or ""
    assert "hdr-opt=1" in x265
    assert "master-display=G(13250" in x265
    assert "max-cll=1000,400" in x265
    assert not any("washed out" in n for n in r.notes)


def test_hdr_to_h264_warns_about_tone_mapping() -> None:
    r = build(make_media(make_video(**HDR10)), ProfileSpec(video_codec="h264"), EncoderChoice(codec="h264", backend="cpu", encoder="libx264"))
    assert "format=yuv420p" in vf(r.args)
    assert any("washed out" in n for n in r.notes)


def test_ten_bit_never_forces_8_bit() -> None:
    r = build(make_media(make_video(**HDR10)), ProfileSpec(ten_bit="never"))
    assert "format=yuv420p" in vf(r.args)
    assert any("washed out" in n for n in r.notes)


def test_dolby_vision_note() -> None:
    r = build(make_media(make_video(**{**HDR10, "hdr_format": "Dolby Vision"})))
    assert any("Dolby Vision" in n for n in r.notes)


def test_unknown_color_tags_are_not_passed() -> None:
    r = build(make_media(make_video(color_primaries="unknown", color_transfer=None)))
    assert "-color_primaries" not in r.args
    assert "-color_trc" not in r.args


# ---------------------------------------------------------------------------
# Hardware backends
# ---------------------------------------------------------------------------


def test_nvenc_full_gpu_pipeline() -> None:
    choice = EncoderChoice(codec="hevc", backend="nvenc", encoder="hevc_nvenc", hw_decode=True)
    r = build(make_media(make_video(width=3840, height=2160)), ProfileSpec(max_resolution=1080), choice)
    assert opt(r.args, "-hwaccel") == "cuda"
    assert vf(r.args) == ["scale_cuda=1920:1080"]
    assert opt(r.args, "-cq") == str(26)
    assert opt(r.args, "-c:v") == "hevc_nvenc"


def test_nvenc_software_decode_uploads_frames_in_software_format() -> None:
    choice = EncoderChoice(codec="hevc", backend="nvenc", encoder="hevc_nvenc", hw_decode=False)
    r = build(choice=choice)
    assert "-hwaccel" not in r.args
    assert vf(r.args) == ["format=nv12"]


def test_vaapi_uses_the_selected_render_device() -> None:
    choice = EncoderChoice(codec="hevc", backend="vaapi", encoder="hevc_vaapi", hw_decode=True, device="/dev/dri/renderD129")
    r = build(choice=choice)
    assert opt(r.args, "-init_hw_device") == "vaapi=va:/dev/dri/renderD129"
    assert opt(r.args, "-hwaccel") == "vaapi"
    assert vf(r.args) == ["scale_vaapi=format=nv12"]
    assert opt(r.args, "-rc_mode") == "CQP"
    assert "-preset" not in r.args


def test_vaapi_software_decode_uploads() -> None:
    choice = EncoderChoice(codec="hevc", backend="vaapi", encoder="hevc_vaapi", hw_decode=False)
    r = build(choice=choice)
    assert "-hwaccel" not in r.args
    assert vf(r.args) == ["format=nv12", "hwupload"]
    assert opt(r.args, "-init_hw_device") == "vaapi=va:/dev/dri/renderD128"  # default device


def test_qsv_derives_from_vaapi() -> None:
    choice = EncoderChoice(codec="av1", backend="qsv", encoder="av1_qsv", hw_decode=True, device="/dev/dri/renderD128")
    r = build(spec=ProfileSpec(video_codec="av1"), choice=choice)
    init = [r.args[i + 1] for i, a in enumerate(r.args) if a == "-init_hw_device"]
    assert init == ["vaapi=va:/dev/dri/renderD128", "qsv=qs@va"]
    assert vf(r.args)[-2:] == ["hwmap=derive_device=qsv", "format=qsv"]
    assert opt(r.args, "-global_quality") is not None


def test_force_software_decode_drops_hwaccel() -> None:
    # The runner retries with this flag when hardware decoding fails.
    choice = EncoderChoice(codec="hevc", backend="vaapi", encoder="hevc_vaapi", hw_decode=True)
    r = build(choice=choice, force_software_decode=True)
    assert "-hwaccel" not in r.args
    assert vf(r.args) == ["format=nv12", "hwupload"]
    assert r.pipeline[0].startswith("Software decode")


def test_amf_av1_has_no_b_frame_qp() -> None:
    choice = EncoderChoice(codec="av1", backend="amf", encoder="av1_amf")
    r = build(spec=ProfileSpec(video_codec="av1"), choice=choice)
    assert "-qp_i" in r.args and "-qp_b" not in r.args


def test_remux_copies_video() -> None:
    choice = EncoderChoice(codec="copy", backend="cpu", encoder="copy")
    r = build(make_media(make_video(codec="hevc")), ProfileSpec(video_codec="copy", container="mp4"), choice)
    assert opt(r.args, "-c:v") == "copy"
    assert opt(r.args, "-tag:v") == "hvc1"
    assert "-vf" not in r.args
    assert r.quality_label is None


def test_extra_args_cannot_touch_io() -> None:
    with pytest.raises(ValueError):
        ProfileSpec(extra_video_args=["-y"])
    r = build(spec=ProfileSpec(extra_video_args=["-g", "240"]))
    assert opt(r.args, "-g") == "240"


def test_extra_args_cannot_add_an_output_file() -> None:
    for bad in (["/media/other.mkv"], ["-g", "240", "/media/other.mkv"], ["out.mkv", "-g", "2"]):
        with pytest.raises(ValueError):
            ProfileSpec(extra_video_args=bad)
    ProfileSpec(extra_video_args=["-x265-params", "aq-mode=3", "-bf", "-1"])  # options with values are fine


# ---------------------------------------------------------------------------
# Audio codecs and copy scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("codec", "encoder"), [("aac", "aac"), ("opus", "libopus"), ("ac3", "ac3"), ("eac3", "eac3"), ("flac", "flac")])
def test_audio_codec_maps_to_its_encoder(codec: str, encoder: str) -> None:
    r = build(spec=ProfileSpec(audio_mode="transcode", audio_codec=codec))
    assert opt(r.args, "-c:a:0") == encoder


def test_flac_has_no_bitrate() -> None:
    r = build(spec=ProfileSpec(audio_mode="transcode", audio_codec="flac"))
    assert "-b:a:0" not in r.args


def test_ac3_is_capped_and_downmixes_7_1_with_a_note() -> None:
    media = make_media(audio=[AudioStream(index=1, codec="truehd", channels=8)])
    r = build(media, ProfileSpec(audio_mode="transcode", audio_codec="ac3", audio_bitrate_kbps=256))
    assert opt(r.args, "-ac:a:0") == "6"
    assert opt(r.args, "-b:a:0") == "640k"  # 3 pairs × 256 = 768, capped at AC-3's 640
    assert any("downmixed" in n for n in r.notes)


def test_widely_playable_scope_converts_opus_in_mp4_with_a_note() -> None:
    media = make_media(audio=[AudioStream(index=1, codec="aac"), AudioStream(index=2, codec="opus")])
    spec = ProfileSpec(container="mp4", audio_mode="copy_compatible", audio_copy_scope="widely_playable")
    r = build(media, spec)
    assert opt(r.args, "-c:a:0") == "copy"
    assert opt(r.args, "-c:a:1") == "aac"
    assert any("doesn't play widely" in n for n in r.notes)


def test_storable_scope_still_copies_opus_into_mp4() -> None:
    media = make_media(audio=[AudioStream(index=1, codec="opus")])
    r = build(media, ProfileSpec(container="mp4", audio_mode="copy_compatible"))
    assert opt(r.args, "-c:a:0") == "copy"


# ---------------------------------------------------------------------------
# Compression preview commands
# ---------------------------------------------------------------------------


def test_preview_uses_the_job_video_settings_with_preroll() -> None:
    spec = ProfileSpec(quality=70)
    pb = build_preview_commands(make_media(), spec, CPU_HEVC, "/media/in.mkv", "/work", [10.0, 300.0], sample_seconds=2.0, preroll_seconds=2.0)
    assert len(pb.steps) == 2 and pb.quality_label == "CRF 24"
    enc = pb.steps[0].encode
    assert opt(enc, "-ss") == "8.000" and opt(enc, "-t") == "4.000"  # 2 s pre-roll + 2 s sample
    assert enc.index("-ss") < enc.index("-i")
    assert opt(enc, "-crf") == "24" and opt(enc, "-c:v") == "libx265"
    assert "-an" in enc and "-progress" not in enc
    assert enc[-1] == "/work/0-sample.mkv"
    # Both frames show the same moment: t in the source, t - start in the sample.
    assert opt(pb.steps[0].extract_original, "-ss") == "10.000"
    assert opt(pb.steps[0].extract_encoded, "-ss") == "2.000"


def test_preview_near_the_start_has_no_negative_seek() -> None:
    pb = build_preview_commands(make_media(), ProfileSpec(), CPU_HEVC, "/in.mkv", "/w", [0.5])
    assert opt(pb.steps[0].encode, "-ss") == "0.000"
    assert opt(pb.steps[0].extract_encoded, "-ss") == "0.500"


def test_preview_scales_a_downsized_encode_back_to_the_source_size() -> None:
    media = make_media(make_video(width=3840, height=2160))
    pb = build_preview_commands(media, ProfileSpec(max_resolution=1080), CPU_HEVC, "/in.mkv", "/w", [60.0])
    assert "scale=3840:2160" in (opt(pb.steps[0].extract_encoded, "-vf") or "")
    assert (pb.width, pb.height) == (3840, 2160)
    assert any("scaled back up" in n for n in pb.notes)


def test_preview_of_a_remux_is_refused() -> None:
    with pytest.raises(BuildError):
        build_preview_commands(make_media(), ProfileSpec(video_codec="copy"), EncoderChoice(codec="copy", backend="cpu", encoder="copy"), "/in.mkv", "/w", [1.0])


@needs_ffmpeg
async def test_preview_commands_run_with_real_ffmpeg(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "in.mp4", seconds=4.0, size="320x240")
    info = await probe_file(str(src))
    pb = build_preview_commands(info, ProfileSpec(video_codec="h264", speed="fast"), EncoderChoice(codec="h264", backend="cpu", encoder="libx264"), str(src), str(tmp_path), [2.5])
    for args in (pb.steps[0].encode, pb.steps[0].extract_original, pb.steps[0].extract_encoded):
        res = subprocess.run(args, capture_output=True, check=False)
        assert res.returncode == 0, res.stderr.decode()
    for name in (pb.steps[0].original_png, pb.steps[0].encoded_png):
        png = (tmp_path / name).read_bytes()
        assert png.startswith(b"\x89PNG")


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["flac", "opus", "ac3", "eac3"])
async def test_new_audio_codecs_mux_into_mp4_for_real(tmp_path: Path, codec: str) -> None:
    src = make_clip(tmp_path / "in.mkv", seconds=1.0)
    info = await probe_file(str(src))
    out = tmp_path / "out.mp4"
    spec = ProfileSpec(container="mp4", video_codec="h264", speed="fast", audio_mode="transcode", audio_codec=codec)
    r = build_command(info, spec, EncoderChoice(codec="h264", backend="cpu", encoder="libx264"), str(src), str(out), "job=1;profile=1;v=1")
    res = subprocess.run(r.args, capture_output=True, check=False)
    assert res.returncode == 0, res.stderr.decode()[-500:]
    back = await probe_file(str(out))
    assert back.audio and back.audio[0].codec == codec
