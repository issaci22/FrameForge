"""Transcoding profile specification (a versioned, validated document)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .codecs import Container, VideoCodec

HwMode = Literal["auto", "cpu", "nvenc", "qsv", "vaapi", "amf"]
Speed = Literal["fast", "balanced", "quality", "max"]
AudioMode = Literal["copy", "copy_compatible", "transcode"]
AudioCodec = Literal["aac", "opus", "ac3", "eac3", "flac"]
# Which source tracks "copy" may keep as-is: anything the container can store, or only what plays widely there.
AudioCopyScope = Literal["storable", "widely_playable"]
RateControl = Literal["quality", "constant_quality", "bitrate"]
EngineName = Literal["ffmpeg", "handbrake"]

PROFILE_SPEC_VERSION = 1


class ProfileSpec(BaseModel):
    """Friendly description of what an output should look like.

    Anything here is translated into concrete encoder arguments by ``ffmpeg_builder``.
    """

    version: int = PROFILE_SPEC_VERSION
    engine: EngineName = "ffmpeg"

    # --- Container / video -------------------------------------------------
    container: Container = Container.MKV
    video_codec: VideoCodec = VideoCodec.HEVC
    quality: int = Field(70, ge=0, le=100, description="0 = smallest, 100 = best quality")
    speed: Speed = "balanced"
    max_resolution: int | None = Field(None, description="Cap on the SHORT side in pixels; None keeps source")
    max_fps: float | None = Field(None, description="Cap frame rate; None keeps source")
    hw_mode: HwMode = "auto"
    allow_cpu_fallback: bool = True
    hw_decode: bool = True
    ten_bit: Literal["auto", "never"] = "auto"

    # --- Audio -------------------------------------------------------------
    audio_mode: AudioMode = "copy_compatible"
    audio_codec: AudioCodec = "aac"
    audio_bitrate_kbps: int = Field(192, ge=32, le=1024, description="Per stereo pair")
    audio_copy_scope: AudioCopyScope = "storable"

    # --- Preservation --------------------------------------------------------
    keep_subtitles: bool = True
    keep_chapters: bool = True
    keep_metadata: bool = True
    keep_attachments: bool = True
    faststart: bool = True  # mp4 only: moov atom at the front for web playback

    # --- Advanced ------------------------------------------------------------
    rate_control: RateControl = "quality"
    constant_quality: int | None = Field(None, ge=0, le=255, description="Raw CRF/CQ/QP override")
    bitrate_kbps: int | None = Field(None, ge=100)
    encoder_preset: str | None = Field(None, description="Raw encoder preset override")
    extra_video_args: list[str] = Field(default_factory=list)

    @field_validator("extra_video_args")
    @classmethod
    def _no_io_args(cls, value: list[str]) -> list[str]:
        forbidden = {"-i", "-y", "-f", "-map", "-progress"}
        previous_is_option = False
        for arg in value:
            if arg in forbidden:
                raise ValueError(f"'{arg}' is managed by FrameForge and can't be used in extra arguments")
            is_option = arg.startswith("-")
            # A bare word that doesn't follow an option would become an extra FFmpeg output file.
            if not is_option and not previous_is_option:
                raise ValueError(f"'{arg}' isn't an option or an option's value. Extra arguments can't add inputs or outputs.")
            previous_is_option = is_option
        return value

    @property
    def is_remux(self) -> bool:
        return self.video_codec == VideoCodec.COPY

    def target_label(self) -> str:
        from .codecs import codec_label

        return "Remux" if self.is_remux else codec_label(self.video_codec.value)
