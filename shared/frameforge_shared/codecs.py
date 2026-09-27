"""Static knowledge about codecs, containers, encoders and hardware backends."""

from __future__ import annotations

from enum import StrEnum


class VideoCodec(StrEnum):
    H264 = "h264"
    HEVC = "hevc"
    AV1 = "av1"
    COPY = "copy"


class Container(StrEnum):
    MKV = "mkv"
    MP4 = "mp4"


class Backend(StrEnum):
    """A family of encoder/decoder implementations."""

    CPU = "cpu"
    NVENC = "nvenc"  # NVIDIA NVENC / NVDEC (CUDA)
    QSV = "qsv"  # Intel Quick Sync
    VAAPI = "vaapi"  # Intel / AMD via VA-API on Linux
    AMF = "amf"  # AMD AMF (Windows-centric; rarely available in Linux containers)


# Order used when a profile asks for "auto" hardware selection.
HW_PREFERENCE: tuple[Backend, ...] = (Backend.NVENC, Backend.QSV, Backend.VAAPI, Backend.AMF)

BACKEND_LABELS: dict[Backend, str] = {
    Backend.CPU: "CPU (software)",
    Backend.NVENC: "NVIDIA NVENC",
    Backend.QSV: "Intel Quick Sync",
    Backend.VAAPI: "VA-API (Intel/AMD)",
    Backend.AMF: "AMD AMF",
}

# FFmpeg encoder name per (codec, backend).
ENCODERS: dict[VideoCodec, dict[Backend, str]] = {
    VideoCodec.H264: {
        Backend.CPU: "libx264",
        Backend.NVENC: "h264_nvenc",
        Backend.QSV: "h264_qsv",
        Backend.VAAPI: "h264_vaapi",
        Backend.AMF: "h264_amf",
    },
    VideoCodec.HEVC: {
        Backend.CPU: "libx265",
        Backend.NVENC: "hevc_nvenc",
        Backend.QSV: "hevc_qsv",
        Backend.VAAPI: "hevc_vaapi",
        Backend.AMF: "hevc_amf",
    },
    VideoCodec.AV1: {
        Backend.CPU: "libsvtav1",
        Backend.NVENC: "av1_nvenc",
        Backend.QSV: "av1_qsv",
        Backend.VAAPI: "av1_vaapi",
        Backend.AMF: "av1_amf",
    },
}

ENCODER_INFO: dict[str, tuple[VideoCodec, Backend]] = {
    enc: (codec, backend) for codec, by_backend in ENCODERS.items() for backend, enc in by_backend.items()
}

# Hardware decoders we try to verify, per backend (source codec names as reported by ffprobe).
HW_DECODE_CODECS: tuple[str, ...] = ("h264", "hevc", "av1", "vp9")

CODEC_LABELS: dict[str, str] = {
    "h264": "H.264",
    "hevc": "H.265",
    "av1": "AV1",
    "vp9": "VP9",
    "vp8": "VP8",
    "mpeg2video": "MPEG-2",
    "mpeg4": "MPEG-4",
    "prores": "ProRes",
    "dnxhd": "DNxHD",
    "copy": "Copy",
}

# Rough "generation" of a codec: higher = more efficient. Used by rules.
CODEC_GENERATION: dict[str, int] = {
    "mpeg1video": 1,
    "mpeg2video": 1,
    "mpeg4": 1,
    "msmpeg4v3": 1,
    "wmv3": 1,
    "vc1": 2,
    "h264": 2,
    "vp8": 2,
    "hevc": 3,
    "vp9": 3,
    "av1": 4,
    "vvc": 4,
}

# Intra/mezzanine codecs: huge files, excellent candidates for compression.
MEZZANINE_CODECS = frozenset({"prores", "dnxhd", "cfhd", "rawvideo", "mjpeg", "ffv1", "utvideo"})

# Audio codecs each container can hold without re-encoding.
AUDIO_COPY_COMPAT: dict[Container, frozenset[str]] = {
    Container.MKV: frozenset(
        {"aac", "mp3", "ac3", "eac3", "dts", "truehd", "flac", "opus", "vorbis", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "alac", "mp2"}
    ),
    Container.MP4: frozenset({"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac"}),
}

# Audio codecs that most players, editors and TVs handle in each container. With the
# "widely playable" copy scope, tracks outside this set are converted instead of copied.
AUDIO_COPY_WIDELY_PLAYABLE: dict[Container, frozenset[str]] = {
    Container.MKV: frozenset({"aac", "mp3", "ac3", "eac3", "dts", "truehd", "flac", "opus", "vorbis"}),
    Container.MP4: frozenset({"aac", "mp3", "ac3", "eac3", "alac"}),
}

# Audio codecs a profile can convert to: profile value -> FFmpeg encoder.
AUDIO_ENCODERS: dict[str, str] = {"aac": "aac", "opus": "libopus", "ac3": "ac3", "eac3": "eac3", "flac": "flac"}
# Highest total bitrate (kb/s) we ask each encoder for; FLAC is lossless and takes no bitrate.
AUDIO_MAX_KBPS: dict[str, int] = {"aac": 1024, "opus": 1024, "ac3": 640, "eac3": 1024}
# Channel limits of the FFmpeg encoders (AC-3 and E-AC-3 stop at 5.1).
AUDIO_MAX_CHANNELS: dict[str, int] = {"ac3": 6, "eac3": 6}
AUDIO_LABELS: dict[str, str] = {"aac": "AAC", "opus": "Opus", "ac3": "AC-3", "eac3": "E-AC-3", "flac": "FLAC"}

TEXT_SUBTITLE_CODECS = frozenset({"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"})
BITMAP_SUBTITLE_CODECS = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"})

CONTAINER_FORMATS: dict[Container, str] = {Container.MKV: "matroska", Container.MP4: "mp4"}
CONTAINER_EXTENSIONS: dict[Container, str] = {Container.MKV: ".mkv", Container.MP4: ".mp4"}

VIDEO_EXTENSIONS = frozenset(
    {".mkv", ".mp4", ".mov", ".m4v", ".flv", ".ts", ".m2ts", ".mts", ".avi", ".webm", ".wmv", ".mpg", ".mpeg", ".mxf"}
)


def codec_label(codec: str | None) -> str:
    if not codec:
        return "—"
    return CODEC_LABELS.get(codec, codec.upper())


def encoder_for(codec: VideoCodec, backend: Backend) -> str | None:
    return ENCODERS.get(codec, {}).get(backend)
