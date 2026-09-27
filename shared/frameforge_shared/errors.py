"""Turn raw FFmpeg failures into explanations a human can act on."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .protocol import Diagnosis


@dataclass(frozen=True)
class _Pattern:
    code: str
    regex: re.Pattern[str]
    title: str
    explanation: str
    causes: tuple[str, ...]
    decode_related: bool = False


def _p(code: str, pattern: str, title: str, explanation: str, *causes: str, decode_related: bool = False) -> _Pattern:
    return _Pattern(code, re.compile(pattern, re.IGNORECASE), title, explanation, causes, decode_related)


# Ordered: first match wins, so put the most specific patterns first.
PATTERNS: tuple[_Pattern, ...] = (
    _p(
        "nvenc_session_limit",
        r"OpenEncodeSessionEx failed: (out of memory|incompatible client key)|No capable devices found.*session|The maximum number of concurrent sessions",
        "The NVIDIA encoder is busy",
        "The GPU refused to open another NVENC encoding session.",
        "Consumer GeForce cards limit how many encodes can run at once (other apps such as OBS or Plex count too)",
        "Lower this node's maximum concurrent jobs",
        "The GPU is out of video memory",
    ),
    _p(
        "nvenc_driver",
        r"Driver does not support the required nvenc API version|minimum required Nvidia driver",
        "The NVIDIA driver is too old for this FFmpeg",
        "FFmpeg needs a newer NVIDIA driver on the host to use NVENC.",
        "Update the NVIDIA driver on the host machine",
    ),
    _p(
        "nvenc_init",
        r"Cannot load libcuda|Cannot load libnvidia-encode|No NVENC capable devices found|CUDA_ERROR_NO_DEVICE|cuInit\(0\) failed|CUDA_ERROR_NOT_INITIALIZED|Could not dynamically load CUDA",
        "FFmpeg could not initialize the NVIDIA encoder",
        "The container can't reach the NVIDIA GPU.",
        "NVIDIA Container Toolkit is not installed/configured on the host",
        "The container was started without GPU access (gpus: all / deploy.resources)",
        "NVIDIA_DRIVER_CAPABILITIES does not include 'video'",
        "The GPU is unavailable or in use by a VM",
    ),
    _p(
        "nvenc_feature",
        r"Provided device doesn't support required NVENC features|10 bit encode not supported|No capable devices found|InitializeEncoder failed: unsupported param",
        "This NVIDIA GPU doesn't support the requested encode",
        "The GPU's NVENC generation can't produce this codec or bit depth.",
        "AV1 NVENC requires an RTX 40-series (Ada) or newer GPU",
        "10-bit H.265 requires Pascal (GTX 10-series) or newer",
    ),
    _p(
        "qsv_init",
        r"Error creating a MFX session|Error initializing an internal MFX session|MFX_ERR_UNSUPPORTED|Error initializing the encoder: unsupported|Failed to create a QSV device|mfxInit",
        "FFmpeg could not initialize Intel Quick Sync",
        "Quick Sync is not usable inside the container.",
        "/dev/dri was not passed to the container",
        "The container user isn't in the render/video group (check PUID/PGID)",
        "The Intel iGPU is disabled in BIOS or a discrete GPU took over",
        "The CPU generation doesn't support this codec (AV1 QSV needs Arc or Meteor Lake+)",
    ),
    _p(
        "vaapi_init",
        r"Failed to initialise VAAPI connection|vaInitialize failed|No VA display found|Failed to open.*renderD|Cannot open.*/dev/dri|Device creation failed|VA-API: failed|No usable encoding entrypoint|No usable encoding profile",
        "FFmpeg could not use VA-API hardware acceleration",
        "The GPU could not be opened through VA-API.",
        "/dev/dri was not passed to the container",
        "The container user lacks permission for /dev/dri/renderD* (check PUID/PGID and groups)",
        "The GPU driver doesn't support this codec for encoding",
    ),
    _p(
        "hw_filter",
        r"Impossible to convert between the formats supported by the filter|Failed to configure output pad|hwaccel initialisation returned error|Failed setup for format|Function not implemented",
        "The hardware decoding pipeline failed",
        "The GPU couldn't decode or process this particular file.",
        "Unusual pixel format or bit depth for hardware decoding",
        "Driver doesn't support decoding this codec profile",
        decode_related=True,
    ),
    _p(
        "unknown_encoder",
        r"Unknown encoder|Encoder not found|Requested encoder .* not found",
        "The encoder isn't available in this FFmpeg build",
        "FFmpeg doesn't include the encoder this profile needs.",
        "Capability detection is out of date. Re-detect the node's capabilities",
    ),
    _p(
        "disk_full",
        r"No space left on device|Disk quota exceeded",
        "The disk is full",
        "There isn't enough free space to write the output file.",
        "The destination (or its .frameforge-tmp folder) is out of space",
        "Free up space or change the library's output location",
    ),
    _p(
        "permission",
        r"Permission denied|Operation not permitted|Read-only file system",
        "Permission denied",
        "FrameForge wasn't allowed to read or write a file.",
        "PUID/PGID don't match the owner of your media",
        "The volume is mounted read-only (:ro)",
        "Network share permissions (SMB/NFS) block writes",
    ),
    _p(
        "missing_file",
        r"No such file or directory",
        "File not found",
        "The file doesn't exist at the path this node was given.",
        "The node doesn't mount the media at the same path. Check the node's path mappings",
        "The file was moved or deleted after the scan",
    ),
    _p(
        "corrupt_input",
        r"moov atom not found|Invalid data found when processing input|EBML header parsing failed|could not find codec parameters|Truncating packet|error while decoding MB",
        "The source file is damaged or incomplete",
        "FFmpeg couldn't read the source cleanly.",
        "An OBS/stream recording that was interrupted (crash, power loss) and never finalized",
        "The file is still being written or copied",
        "Disk or network errors while reading",
    ),
    _p(
        "pixel_format",
        r"Incompatible pixel format|does not support pixel format|Specified pixel format .* is invalid or not supported",
        "Unsupported pixel format",
        "The chosen encoder can't handle this source's color format.",
        "The source uses 4:2:2/4:4:4 or 12-bit color, which many hardware encoders can't encode",
        "Try a CPU encoder for this file",
    ),
    _p(
        "out_of_memory",
        r"Cannot allocate memory|out of memory",
        "Out of memory",
        "The system ran out of RAM or GPU memory.",
        "Too many concurrent jobs on this node",
        "Very high resolution source (8K) with a memory-hungry encoder preset",
    ),
)


def diagnose(stderr_tail: list[str], exit_code: int | None) -> Diagnosis:
    """Match the tail of FFmpeg's stderr against known failure signatures."""
    text = "\n".join(stderr_tail)
    for pattern in PATTERNS:
        if pattern.regex.search(text):
            return Diagnosis(
                code=pattern.code,
                title=pattern.title,
                explanation=pattern.explanation,
                causes=list(pattern.causes),
                technical=_last_error_lines(stderr_tail),
            )
    return Diagnosis(
        code="ffmpeg_failed",
        title="Transcoding failed",
        explanation=f"FFmpeg stopped with exit code {exit_code}." if exit_code is not None else "FFmpeg stopped unexpectedly.",
        causes=["See the technical details below for FFmpeg's own error message"],
        technical=_last_error_lines(stderr_tail),
    )


def is_decode_related(diagnosis: Diagnosis) -> bool:
    return any(p.code == diagnosis.code and p.decode_related for p in PATTERNS)


def _last_error_lines(lines: list[str], limit: int = 12) -> str:
    interesting = [ln for ln in lines if ln.strip()]
    return "\n".join(interesting[-limit:])


def diagnosis(code: str, title: str, explanation: str, *causes: str, technical: str | None = None) -> Diagnosis:
    return Diagnosis(code=code, title=title, explanation=explanation, causes=list(causes), technical=technical)
