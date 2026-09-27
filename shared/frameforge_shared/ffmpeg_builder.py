"""Translate (source media, profile, encoder choice) into an FFmpeg argument list.

This is the ONLY place FFmpeg command lines are constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .codecs import (
    AUDIO_COPY_COMPAT,
    AUDIO_COPY_WIDELY_PLAYABLE,
    AUDIO_ENCODERS,
    AUDIO_LABELS,
    AUDIO_MAX_CHANNELS,
    AUDIO_MAX_KBPS,
    BITMAP_SUBTITLE_CODECS,
    CONTAINER_FORMATS,
    TEXT_SUBTITLE_CODECS,
    Backend,
    Container,
    VideoCodec,
    codec_label,
)
from .media import FRAMEFORGE_TAG, MediaInfo, VideoStream
from .profile import ProfileSpec
from .protocol import EncoderChoice
from .quality import encoder_preset, map_quality
from .tools import ffmpeg_path

FPS_CAP_TOLERANCE = 0.5
MAX_AUDIO_BITRATE_KBPS = 1024
PREVIEW_CONTAINER = Container.MKV  # preview samples are never kept; MKV takes every codec without tags


class BuildError(Exception):
    """The requested conversion is impossible for this source."""


@dataclass
class BuildResult:
    args: list[str]
    notes: list[str] = field(default_factory=list)
    pipeline: list[str] = field(default_factory=list)  # human-readable stages
    quality_label: str | None = None
    target_width: int | None = None
    target_height: int | None = None

    def command_line(self) -> str:
        def quote(a: str) -> str:
            return f'"{a}"' if (" " in a or not a) else a

        return " ".join(quote(a) for a in self.args)


def _even(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


def _target_size(v: VideoStream, spec: ProfileSpec) -> tuple[int, int] | None:
    """Return (w, h) in display orientation if scaling is needed. Caps the short side, never upscales."""
    if not spec.max_resolution:
        return None
    w, h = v.display_width, v.display_height
    short = min(w, h)
    if short <= spec.max_resolution or short <= 0:
        return None
    factor = spec.max_resolution / short
    return _even(w * factor), _even(h * factor)


def _use_ten_bit(v: VideoStream, spec: ProfileSpec, codec: VideoCodec) -> bool:
    return spec.ten_bit == "auto" and codec in (VideoCodec.HEVC, VideoCodec.AV1) and v.bit_depth > 8


def _color_args(v: VideoStream) -> list[str]:
    args: list[str] = []
    for opt, value in (
        ("-color_primaries", v.color_primaries),
        ("-color_trc", v.color_transfer),
        ("-colorspace", v.color_space),
        ("-color_range", v.color_range),
    ):
        if value and value not in ("unknown", "reserved"):
            args += [opt, value]
    return args


def _x265_params(v: VideoStream, ten_bit: bool) -> str:
    params = ["log-level=error"]
    if v.is_hdr and ten_bit:
        params.append("hdr-opt=1")
        params.append("repeat-headers=1")
        if v.color_primaries:
            params.append(f"colorprim={v.color_primaries}")
        if v.color_transfer:
            params.append(f"transfer={v.color_transfer}")
        if v.color_space:
            params.append(f"colormatrix={v.color_space}")
        if v.mastering_display:
            params.append(f"master-display={v.mastering_display}")
        if v.content_light:
            params.append(f"max-cll={v.content_light}")
    return ":".join(params)


def _video_filters(
    backend: Backend,
    hw_frames: bool,
    size: tuple[int, int] | None,
    ten_bit: bool,
    pipeline: list[str],
) -> list[str]:
    filters: list[str] = []
    w_h = f"{size[0]}:{size[1]}" if size else None
    if backend == Backend.NVENC and hw_frames:
        if w_h:
            filters.append(f"scale_cuda={w_h}")
            pipeline.append(f"GPU scale → {size[0]}×{size[1]}")
        return filters
    if backend in (Backend.VAAPI, Backend.QSV) and hw_frames:
        parts = []
        if w_h:
            parts += [f"w={size[0]}", f"h={size[1]}"]
            pipeline.append(f"GPU scale → {size[0]}×{size[1]}")
        parts.append(f"format={'p010' if ten_bit else 'nv12'}")
        filters.append("scale_vaapi=" + ":".join(parts))
        if backend == Backend.QSV:
            filters.append("hwmap=derive_device=qsv")
            filters.append("format=qsv")
        return filters

    # Software frames.
    if w_h:
        filters.append(f"scale={w_h}:flags=lanczos")
        pipeline.append(f"Scale → {size[0]}×{size[1]}")
    if backend == Backend.CPU:
        filters.append(f"format={'yuv420p10le' if ten_bit else 'yuv420p'}")
    elif backend in (Backend.NVENC, Backend.AMF):
        filters.append(f"format={'p010le' if ten_bit else 'nv12'}")
    elif backend == Backend.VAAPI:
        filters.append(f"format={'p010' if ten_bit else 'nv12'}")
        filters.append("hwupload")
    elif backend == Backend.QSV:
        filters.append(f"format={'p010' if ten_bit else 'nv12'}")
        filters.append("hwupload=extra_hw_frames=64")
        filters.append("format=qsv")
    return filters


def _encoder_args(
    spec: ProfileSpec,
    codec: VideoCodec,
    backend: Backend,
    encoder: str,
    v: VideoStream,
    ten_bit: bool,
    container: Container,
) -> tuple[list[str], str]:
    override = spec.constant_quality if spec.rate_control == "constant_quality" else None
    q = map_quality(spec.quality, codec, backend, override)
    preset = spec.encoder_preset or encoder_preset(codec, backend, spec.speed)
    args = ["-c:v", encoder]
    bitrate_mode = spec.rate_control == "bitrate" and spec.bitrate_kbps
    label = f"{q.label}" if not bitrate_mode else f"{spec.bitrate_kbps} kb/s"

    if backend == Backend.CPU:
        if preset:
            args += ["-preset", preset]
        if bitrate_mode:
            args += ["-b:v", f"{spec.bitrate_kbps}k", "-maxrate", f"{int(spec.bitrate_kbps * 1.5)}k", "-bufsize", f"{spec.bitrate_kbps * 2}k"]
        else:
            args += ["-crf", str(q.value)]
        if codec == VideoCodec.H264:
            args += ["-profile:v", "high"]
        elif codec == VideoCodec.HEVC:
            args += ["-x265-params", _x265_params(v, ten_bit)]
    elif backend == Backend.NVENC:
        if preset:
            args += ["-preset", preset]
        args += ["-tune", "hq", "-rc", "vbr"]
        if bitrate_mode:
            args += ["-b:v", f"{spec.bitrate_kbps}k", "-maxrate", f"{int(spec.bitrate_kbps * 1.5)}k", "-bufsize", f"{spec.bitrate_kbps * 2}k"]
        else:
            args += ["-cq", str(q.value), "-b:v", "0"]
        args += ["-spatial-aq", "1", "-rc-lookahead", "20"]
        if codec in (VideoCodec.H264, VideoCodec.HEVC):
            args += ["-temporal-aq", "1"]
        if codec == VideoCodec.HEVC and ten_bit:
            args += ["-profile:v", "main10"]
        elif codec == VideoCodec.H264:
            args += ["-profile:v", "high"]
    elif backend == Backend.QSV:
        if preset:
            args += ["-preset", preset]
        if bitrate_mode:
            args += ["-b:v", f"{spec.bitrate_kbps}k", "-maxrate", f"{int(spec.bitrate_kbps * 1.5)}k"]
        else:
            args += ["-global_quality", str(q.value)]
        if codec == VideoCodec.HEVC and ten_bit:
            args += ["-profile:v", "main10"]
    elif backend == Backend.VAAPI:
        if bitrate_mode:
            args += ["-rc_mode", "VBR", "-b:v", f"{spec.bitrate_kbps}k", "-maxrate", f"{int(spec.bitrate_kbps * 1.5)}k"]
        else:
            args += ["-rc_mode", "CQP", "-qp", str(q.value)]
    elif backend == Backend.AMF:
        if preset:
            args += ["-quality", preset]
        if bitrate_mode:
            args += ["-rc", "vbr_peak", "-b:v", f"{spec.bitrate_kbps}k", "-maxrate", f"{int(spec.bitrate_kbps * 1.5)}k"]
        else:
            args += ["-rc", "cqp", "-qp_i", str(q.value), "-qp_p", str(q.value)]
            if codec != VideoCodec.AV1:
                args += ["-qp_b", str(q.value)]

    if codec == VideoCodec.HEVC and container == Container.MP4:
        args += ["-tag:v", "hvc1"]  # Apple/QuickTime compatibility
    args += _color_args(v)
    args += spec.extra_video_args
    return args, label


def _audio_args(spec: ProfileSpec, source: MediaInfo, container: Container, notes: list[str]) -> tuple[list[str], list[str]]:
    maps: list[str] = []
    codecs: list[str] = []
    storable = AUDIO_COPY_COMPAT[container]
    playable = AUDIO_COPY_WIDELY_PLAYABLE[container] if spec.audio_copy_scope == "widely_playable" else storable
    target = spec.audio_codec
    target_label = AUDIO_LABELS.get(target, target.upper())
    for out_idx, a in enumerate(source.audio):
        maps += ["-map", f"0:{a.index}"]
        copy = False
        if spec.audio_mode in ("copy", "copy_compatible"):
            copy = a.codec in playable
            if not copy and a.codec in storable:
                notes.append(f"Audio track {out_idx + 1} ({a.codec}) doesn't play widely in {container.value.upper()}; converted to {target_label}")
            elif not copy and spec.audio_mode == "copy":
                notes.append(f"Audio track {out_idx + 1} ({a.codec}) can't be stored in {container.value.upper()}; converted to {target_label}")
        if copy:
            codecs += [f"-c:a:{out_idx}", "copy"]
            continue
        channels = a.channels
        max_channels = AUDIO_MAX_CHANNELS.get(target)
        codecs += [f"-c:a:{out_idx}", AUDIO_ENCODERS.get(target, "aac")]
        if max_channels and channels > max_channels:
            codecs += [f"-ac:a:{out_idx}", str(max_channels)]
            notes.append(f"Audio track {out_idx + 1} has {channels} channels; {target_label} holds at most 5.1, so it was downmixed")
            channels = max_channels
        if target == "flac":
            continue  # lossless: no bitrate
        pairs = max(1.0, channels / 2)
        bitrate = min(AUDIO_MAX_KBPS.get(target, MAX_AUDIO_BITRATE_KBPS), int(spec.audio_bitrate_kbps * pairs))
        codecs += [f"-b:a:{out_idx}", f"{bitrate}k"]
        if target == "opus" and channels > 2:
            codecs += [f"-mapping_family:a:{out_idx}", "1"]
    return maps, codecs


def _subtitle_args(spec: ProfileSpec, source: MediaInfo, container: Container, notes: list[str]) -> list[str]:
    if not source.subtitles:
        return []
    if not spec.keep_subtitles:
        notes.append(f"Removed {len(source.subtitles)} subtitle track(s) (profile setting)")
        return []
    args: list[str] = []
    out_idx = 0
    dropped = 0
    for s in source.subtitles:
        if container == Container.MP4:
            if s.codec in TEXT_SUBTITLE_CODECS:
                args += ["-map", f"0:{s.index}", f"-c:s:{out_idx}", "mov_text"]
                out_idx += 1
            else:
                dropped += 1
        else:
            codec = "srt" if s.codec == "mov_text" else "copy"
            args += ["-map", f"0:{s.index}", f"-c:s:{out_idx}", codec]
            out_idx += 1
    if dropped:
        notes.append(f"Dropped {dropped} image-based subtitle track(s): MP4 can't hold them (use MKV to keep them)")
    return args


@dataclass
class VideoSection:
    """The video half of a command, shared by real jobs and compression previews."""

    input_args: list[str]  # hardware device setup; goes before ``-i``
    video_args: list[str]  # filters + encoder options; goes after the video ``-map``
    quality_label: str | None
    size: tuple[int, int] | None


def _video_section(
    source: MediaInfo, spec: ProfileSpec, choice: EncoderChoice, container: Container, force_software_decode: bool, notes: list[str], pipeline: list[str]
) -> VideoSection:
    v = source.video
    if v is None:
        raise BuildError("The source has no video stream")
    remux = choice.encoder == "copy"
    backend = Backend(choice.backend)
    codec = VideoCodec(choice.codec) if not remux else VideoCodec.COPY
    hw_frames = bool(choice.hw_decode and not force_software_decode and not remux)
    device = choice.device or "/dev/dri/renderD128"

    pre: list[str] = []
    if not remux:
        if backend == Backend.NVENC and hw_frames:
            pre += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
        elif backend == Backend.VAAPI:
            pre += ["-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va"]
            if hw_frames:
                pre += ["-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi"]
        elif backend == Backend.QSV:
            pre += ["-init_hw_device", f"vaapi=va:{device}", "-init_hw_device", "qsv=qs@va", "-filter_hw_device", "qs"]
            if hw_frames:
                pre += ["-hwaccel", "vaapi", "-hwaccel_device", "va", "-hwaccel_output_format", "vaapi"]

    decode_label = "Hardware decode" if hw_frames else "Software decode"
    pipeline.append(f"{decode_label} ({codec_label(v.codec)})")

    args: list[str] = []
    quality_label: str | None = None
    size: tuple[int, int] | None = None
    if remux:
        args += ["-c:v", "copy"]
        if v.codec == "hevc" and container == Container.MP4:
            args += ["-tag:v", "hvc1"]
        pipeline.append("Copy video (no re-encode)")
    else:
        ten_bit = _use_ten_bit(v, spec, codec)
        size = _target_size(v, spec)
        filters = _video_filters(backend, hw_frames, size, ten_bit, pipeline)
        if filters:
            args += ["-vf", ",".join(filters)]
        if spec.max_fps and v.fps and v.fps > spec.max_fps + FPS_CAP_TOLERANCE:
            args += ["-fpsmax", f"{spec.max_fps:g}"]
            pipeline.append(f"Limit to {spec.max_fps:g} fps")
        enc_args, quality_label = _encoder_args(spec, codec, backend, choice.encoder, v, ten_bit, container)
        args += enc_args
        pipeline.append(f"Encode {codec_label(codec.value)} on {backend.value.upper()} ({quality_label})")
        if v.is_hdr and not ten_bit:
            notes.append(f"{v.hdr_format} source encoded as 8-bit {codec_label(codec.value)}: no tone mapping is applied, colors may look washed out. Use H.265 or AV1 to keep HDR.")
        if v.hdr_format == "Dolby Vision":
            notes.append("Dolby Vision dynamic metadata is not carried over; the HDR10/HLG base layer is kept.")
    return VideoSection(pre, args, quality_label, size)


def build_command(
    source: MediaInfo,
    spec: ProfileSpec,
    choice: EncoderChoice,
    input_path: str,
    output_path: str,
    tag: str,
    force_software_decode: bool = False,
) -> BuildResult:
    v = source.video
    if v is None:
        raise BuildError("The source has no video stream")
    container = Container(spec.container)
    notes: list[str] = []
    pipeline: list[str] = []

    args: list[str] = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-loglevel", "info", "-progress", "pipe:1", "-nostats"]
    video = _video_section(source, spec, choice, container, force_software_decode, notes, pipeline)
    args += video.input_args
    args += ["-i", input_path]
    args += ["-map", f"0:{v.index}"]
    args += video.video_args
    quality_label, size = video.quality_label, video.size

    # --- Audio / subtitles / attachments ------------------------------------
    a_maps, a_codecs = _audio_args(spec, source, container, notes)
    args += a_maps + a_codecs
    args += _subtitle_args(spec, source, container, notes)
    if source.attachment_count:
        if container == Container.MKV and spec.keep_attachments and source.container in ("mkv", "webm"):
            args += ["-map", "0:t?", "-c:t", "copy"]
        else:
            notes.append(f"Dropped {source.attachment_count} attachment(s)/cover image(s)")
    if source.data_stream_count:
        notes.append(f"Dropped {source.data_stream_count} data stream(s) (e.g. timecode tracks); not needed for playback")

    # --- Metadata ---------------------------------------------------------------
    args += ["-map_chapters", "0" if spec.keep_chapters else "-1"]
    args += ["-map_metadata", "0" if spec.keep_metadata else "-1"]
    args += ["-metadata", f"{FRAMEFORGE_TAG}={tag}"]
    if container == Container.MP4:
        flags = "+use_metadata_tags"
        if spec.faststart:
            flags += "+faststart"
        args += ["-movflags", flags]

    args += ["-max_muxing_queue_size", "4096", "-f", CONTAINER_FORMATS[container], output_path]

    return BuildResult(
        args=args,
        notes=notes,
        pipeline=pipeline,
        quality_label=quality_label,
        target_width=size[0] if size else None,
        target_height=size[1] if size else None,
    )


def decode_check_args(path: str, from_end: bool, seconds: int) -> list[str]:
    """Decode a few seconds of video at the start or the end, discarding the frames."""
    seek = ["-sseof", f"-{seconds}"] if from_end else []
    return [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error", *seek, "-i", path, "-t", str(seconds), "-map", "0:v:0", "-f", "null", "-"]


# ---------------------------------------------------------------------------
# Compression previews
# ---------------------------------------------------------------------------


@dataclass
class PreviewStep:
    position: float  # seconds into the source
    encode: list[str]  # short video-only encode with the job's exact video settings
    sample_path: str
    extract_original: list[str]  # one PNG of the source frame at ``position``
    extract_encoded: list[str]  # one PNG of the encoded frame at the same moment, at the source's display size
    original_png: str
    encoded_png: str
    sample_seconds: float


@dataclass
class PreviewBuild:
    steps: list[PreviewStep]
    notes: list[str] = field(default_factory=list)
    quality_label: str | None = None
    width: int = 0
    height: int = 0


def _png_args(input_seek: float, input_path: str, output_path: str, video_filter: str | None) -> list[str]:
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{input_seek:.3f}", "-i", input_path, "-map", "0:v:0", "-frames:v", "1"]
    if video_filter:
        args += ["-vf", video_filter]
    return args + ["-f", "image2", "-c:v", "png", "-compression_level", "3", output_path]


def build_preview_commands(
    source: MediaInfo,
    spec: ProfileSpec,
    choice: EncoderChoice,
    input_path: str,
    work_dir: str,
    positions: list[float],
    sample_seconds: float = 2.0,
    preroll_seconds: float = 2.0,
) -> PreviewBuild:
    """Commands that encode short samples exactly like a real job would, and grab matching frames.

    Each sample starts ``preroll_seconds`` before the inspected moment, so the encoder has settled
    (the first frame of any encode is a key frame and looks better than typical frames).
    """
    v = source.video
    if v is None:
        raise BuildError("The source has no video stream")
    if choice.encoder == "copy":
        raise BuildError("Copying the video doesn't change it, so there's nothing to preview")
    notes: list[str] = []
    display = (v.display_width, v.display_height)
    if v.is_hdr:
        notes.append(f"{v.hdr_format} frames are shown without tone mapping, so both sides look flatter than on an HDR screen.")
    steps: list[PreviewStep] = []
    quality_label: str | None = None
    for i, pos in enumerate(positions):
        pipeline: list[str] = []
        video = _video_section(source, spec, choice, PREVIEW_CONTAINER, False, notes if i == 0 else [], pipeline)
        quality_label = video.quality_label
        start = max(0.0, pos - preroll_seconds)
        sample = f"{work_dir}/{i}-sample.mkv"
        encode = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *video.input_args, "-ss", f"{start:.3f}", "-i", input_path]
        encode += ["-t", f"{pos - start + sample_seconds:.3f}", "-map", f"0:{v.index}", *video.video_args, "-an", "-sn", "-dn", "-map_metadata", "-1", "-f", CONTAINER_FORMATS[PREVIEW_CONTAINER], sample]
        scale_back = f"scale={display[0]}:{display[1]}:flags=lanczos" if video.size else None
        original_png = f"{i}-original.png"
        encoded_png = f"{i}-encoded.png"
        steps.append(
            PreviewStep(
                position=pos,
                encode=encode,
                sample_path=sample,
                extract_original=_png_args(pos, input_path, f"{work_dir}/{original_png}", None),
                extract_encoded=_png_args(pos - start, sample, f"{work_dir}/{encoded_png}", scale_back),
                original_png=original_png,
                encoded_png=encoded_png,
                sample_seconds=pos - start + sample_seconds,
            )
        )
    if spec.max_resolution and steps and _target_size(v, spec):
        notes.append("This profile lowers the resolution. The encoded frame is scaled back up to the source size so you can compare them directly.")
    return PreviewBuild(steps=steps, notes=notes, quality_label=quality_label, width=display[0], height=display[1])
