"""Pick the concrete encoder for a profile on a given node."""

from __future__ import annotations

from dataclasses import dataclass

from .codecs import AUDIO_LABELS, HW_PREFERENCE, Backend, VideoCodec, encoder_for
from .media import MediaInfo
from .profile import ProfileSpec
from .protocol import EncoderChoice, NodeCapabilities


@dataclass
class Selection:
    choice: EncoderChoice | None
    reason: str | None = None  # why nothing could be chosen


def _backend_order(spec: ProfileSpec) -> list[Backend]:
    if spec.hw_mode == "cpu":
        return [Backend.CPU]
    if spec.hw_mode == "auto":
        order = list(HW_PREFERENCE)
    else:
        order = [Backend(spec.hw_mode)]
    if spec.allow_cpu_fallback:
        order.append(Backend.CPU)
    return order


def _hw_decode_possible(backend: Backend, source: MediaInfo | None, caps: NodeCapabilities, target: VideoCodec) -> bool:
    if backend == Backend.CPU or source is None or source.video is None:
        return False
    v = source.video
    if v.rotation:
        return False  # autorotate filters don't work on hardware frames
    if target == VideoCodec.H264 and v.bit_depth > 8:
        return False  # H.264 hardware encoders take 8-bit; needs a software format conversion
    decode_backend = "vaapi" if backend in (Backend.QSV, Backend.VAAPI) else backend.value
    if backend == Backend.AMF:
        return False
    return v.codec in caps.verified_decoders(decode_backend)


def select_encoder(
    spec: ProfileSpec,
    caps: NodeCapabilities,
    source: MediaInfo | None = None,
) -> Selection:
    if spec.engine != "ffmpeg":
        return Selection(None, f"The {spec.engine} engine is not available in this version")
    if not caps.engines.get("ffmpeg"):
        return Selection(None, "FFmpeg is not available on this node")
    if spec.audio_codec not in caps.verified_audio_codecs():
        return Selection(None, f"No working {AUDIO_LABELS.get(spec.audio_codec, spec.audio_codec)} audio encoder on this node")
    if spec.is_remux:
        return Selection(EncoderChoice(codec="copy", backend="cpu", encoder="copy"))

    verified = caps.verified_encoders()
    codec = VideoCodec(spec.video_codec)
    for backend in _backend_order(spec):
        name = encoder_for(codec, backend)
        if name and name in verified:
            hw_decode = spec.hw_decode and _hw_decode_possible(backend, source, caps, codec)
            device = caps.render_device if backend in (Backend.QSV, Backend.VAAPI) else None
            return Selection(
                EncoderChoice(codec=codec.value, backend=backend.value, encoder=name, hw_decode=hw_decode, device=device)
            )

    wanted = "any hardware encoder" if spec.hw_mode == "auto" else spec.hw_mode.upper()
    if spec.hw_mode == "cpu" or spec.allow_cpu_fallback:
        return Selection(None, f"No working {codec.value.upper()} encoder on this node")
    return Selection(None, f"No working {wanted} {codec.value.upper()} encoder on this node (CPU fallback disabled)")
