"""Node <-> server websocket protocol.

Every frame is a JSON object ``{"type": <str>, "data": {...}}``. ``data`` is validated with
the models below. Adding optional fields is backwards compatible; anything else requires
bumping ``PROTOCOL_VERSION``.

Newer abilities are advertised as ``Hello.features``. The server only sends a node work that
needs a feature the node listed, so older nodes keep working without a protocol bump.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .media import MediaInfo
from .profile import ProfileSpec

# ---------------------------------------------------------------------------
# Capabilities & metrics
# ---------------------------------------------------------------------------


class GpuInfo(BaseModel):
    index: int
    vendor: Literal["nvidia", "intel", "amd", "unknown"]
    name: str
    vram_total_mb: int | None = None
    driver: str | None = None
    device: str | None = None  # e.g. /dev/dri/renderD128 or CUDA index


class EncoderCapability(BaseModel):
    name: str  # ffmpeg encoder, e.g. hevc_nvenc
    codec: str  # h264 / hevc / av1
    backend: str  # cpu / nvenc / qsv / vaapi / amf
    verified: bool
    error: str | None = None


class AudioEncoderCapability(BaseModel):
    name: str  # ffmpeg encoder, e.g. libopus
    codec: str  # profile audio codec: aac / opus / ac3 / eac3 / flac
    verified: bool
    error: str | None = None


class DecoderCapability(BaseModel):
    backend: str
    codec: str
    verified: bool
    error: str | None = None


class StorageInfo(BaseModel):
    path: str
    total_bytes: int
    free_bytes: int


class NodeCapabilities(BaseModel):
    cpu_model: str = "unknown"
    cpu_threads: int = 1
    ram_total_mb: int = 0
    os: str = "unknown"
    hostname: str = "unknown"
    ffmpeg_version: str | None = None
    engines: dict[str, bool] = Field(default_factory=lambda: {"ffmpeg": False, "handbrake": False})
    gpus: list[GpuInfo] = Field(default_factory=list)
    encoders: list[EncoderCapability] = Field(default_factory=list)
    decoders: list[DecoderCapability] = Field(default_factory=list)
    # None = a node from before audio encoders were verified (it can do AAC and Opus).
    audio_encoders: list[AudioEncoderCapability] | None = None
    render_device: str | None = None  # VA-API/QSV device path, if any
    storage: list[StorageInfo] = Field(default_factory=list)
    recommended_concurrency: int = 1
    notes: list[str] = Field(default_factory=list)
    detected_at: float = 0.0

    def verified_encoders(self) -> set[str]:
        return {e.name for e in self.encoders if e.verified}

    def verified_decoders(self, backend: str) -> set[str]:
        return {d.codec for d in self.decoders if d.verified and d.backend == backend}

    def verified_audio_codecs(self) -> set[str]:
        if self.audio_encoders is None:
            return set(LEGACY_AUDIO_CODECS)
        return {a.codec for a in self.audio_encoders if a.verified}


# Audio codecs every node could encode before audio encoders were verified individually.
LEGACY_AUDIO_CODECS = ("aac", "opus")


class GpuMetrics(BaseModel):
    index: int
    utilization: float | None = None
    encoder_utilization: float | None = None
    decoder_utilization: float | None = None
    vram_used_mb: int | None = None
    vram_total_mb: int | None = None
    temperature_c: float | None = None


class NodeMetrics(BaseModel):
    timestamp: float
    cpu_percent: float
    ram_used_mb: int
    ram_total_mb: int
    load_1m: float | None = None
    gpus: list[GpuMetrics] = Field(default_factory=list)
    disk_read_bps: float | None = None
    disk_write_bps: float | None = None
    net_rx_bps: float | None = None
    net_tx_bps: float | None = None


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


class EncoderChoice(BaseModel):
    codec: str  # target video codec or "copy"
    backend: str  # cpu / nvenc / ...
    encoder: str  # ffmpeg encoder name or "copy"
    hw_decode: bool = False
    device: str | None = None


class ValidationThresholds(BaseModel):
    duration_tolerance_pct: float = 2.0
    min_output_bytes: int = 1024 * 1024
    max_size_ratio: float | None = 1.0  # output/source; None disables
    fail_if_larger: bool = True  # reject (keep the original) rather than just warn
    require_audio_if_source_has_audio: bool = True


class FinalizePlan(BaseModel):
    """Where the output goes and what happens to the original. All paths are SERVER paths."""

    source: str
    temp_output: str
    final_output: str
    original_action: Literal["keep", "delete", "backup"]
    backup_path: str | None = None


class JobAssignment(BaseModel):
    job_id: int
    source_path: str
    profile: ProfileSpec
    encoder: EncoderChoice
    plan: FinalizePlan
    thresholds: ValidationThresholds
    source_media: MediaInfo | None = None
    frameforge_tag: str


class JobProgress(BaseModel):
    job_id: int
    percent: float
    frame: int | None = None
    fps: float | None = None
    speed: float | None = None
    bitrate_kbps: float | None = None
    out_size: int | None = None
    elapsed: float = 0.0
    eta: float | None = None


class JobStage(BaseModel):
    job_id: int
    stage: Literal["preparing", "transcoding", "validating", "finalizing"]
    message: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class JobLogLines(BaseModel):
    job_id: int
    lines: list[str]


class Diagnosis(BaseModel):
    code: str
    title: str
    explanation: str
    causes: list[str] = Field(default_factory=list)
    technical: str | None = None


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    detail: str
    severity: Literal["error", "warning"] = "error"


class ValidationReport(BaseModel):
    passed: bool
    checks: list[ValidationCheck] = Field(default_factory=list)


class JobResult(BaseModel):
    job_id: int
    status: Literal["completed", "failed", "cancelled", "rejected"]
    reject_reason: str | None = None
    diagnosis: Diagnosis | None = None
    validation: ValidationReport | None = None
    source_size: int | None = None
    output_size: int | None = None
    output_media: MediaInfo | None = None
    final_path: str | None = None  # server path
    output_fingerprint: str | None = None
    output_mtime: float | None = None
    encoder_used: str | None = None
    hw_decode_used: bool | None = None
    notes: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    # Where the original ended up (FEATURE_ORIGINAL_REPORT). Paths are server paths.
    original_disposition: Literal["deleted", "backed_up", "kept", "rescued", "unknown"] | None = None
    original_path: str | None = None
    original_size: int | None = None
    original_fingerprint: str | None = None


# ---------------------------------------------------------------------------
# Compression previews (FEATURE_PREVIEW)
# ---------------------------------------------------------------------------


class PreviewRequest(BaseModel):
    request_id: str
    source_path: str  # server path
    profile: ProfileSpec
    encoder: EncoderChoice
    source_media: MediaInfo
    positions: list[float]  # seconds into the source
    sample_seconds: float = 2.0
    preroll_seconds: float = 2.0


class PreviewCancel(BaseModel):
    request_id: str


class PreviewProgress(BaseModel):
    request_id: str
    done: int
    total: int
    message: str | None = None


class PreviewChunk(BaseModel):
    """A slice of one preview image. Images are small and never media files, so they travel over the socket."""

    request_id: str
    name: str  # e.g. "0-encoded.png"
    offset: int
    data: str  # base64
    last: bool = False


class PreviewSample(BaseModel):
    index: int
    position: float
    original: str  # image names, as sent in chunks
    encoded: str
    width: int
    height: int
    encoded_bytes: int  # size of the encoded sample (video only)
    sample_seconds: float


class PreviewResult(BaseModel):
    request_id: str
    ok: bool
    samples: list[PreviewSample] = Field(default_factory=list)
    encoder: str | None = None
    quality_label: str | None = None
    notes: list[str] = Field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# Envelope payloads
# ---------------------------------------------------------------------------


class Hello(BaseModel):
    protocol: int
    node_version: str
    capabilities: NodeCapabilities
    active_jobs: list[int] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)


class Heartbeat(BaseModel):
    metrics: NodeMetrics
    active_jobs: list[int] = Field(default_factory=list)


class NodeLog(BaseModel):
    lines: list[str]


class PathMapping(BaseModel):
    server: str
    node: str


class Welcome(BaseModel):
    node_id: int
    name: str
    max_concurrency: int
    path_mappings: list[PathMapping] = Field(default_factory=list)


class CancelJob(BaseModel):
    job_id: int


class RecoverJob(BaseModel):
    job_id: int
    plan: FinalizePlan


class NodeConfig(BaseModel):
    max_concurrency: int
    path_mappings: list[PathMapping] = Field(default_factory=list)


# Message type names
NODE_HELLO = "hello"
NODE_HEARTBEAT = "heartbeat"
NODE_JOB_STAGE = "job.stage"
NODE_JOB_PROGRESS = "job.progress"
NODE_JOB_LOG = "job.log"
NODE_JOB_RESULT = "job.result"
NODE_LOG = "node.log"
NODE_CAPABILITIES = "capabilities"

SERVER_WELCOME = "welcome"
SERVER_ASSIGN = "job.assign"
SERVER_CANCEL = "job.cancel"
SERVER_RECOVER = "job.recover"
SERVER_CONFIG = "config"
SERVER_REDETECT = "capabilities.redetect"
SERVER_ERROR = "error"
SERVER_PREVIEW = "preview.request"
SERVER_PREVIEW_CANCEL = "preview.cancel"
NODE_PREVIEW_PROGRESS = "preview.progress"
NODE_PREVIEW_CHUNK = "preview.chunk"
NODE_PREVIEW_RESULT = "preview.result"

# Node features (Hello.features)
FEATURE_PREVIEW = "preview.v1"  # compression previews
FEATURE_AUDIO_V2 = "audio.v2"  # AC-3 / E-AC-3 / FLAC audio and audio_copy_scope
FEATURE_ORIGINAL_REPORT = "result.original.v1"  # JobResult.original_* fields
NODE_FEATURES = (FEATURE_PREVIEW, FEATURE_AUDIO_V2, FEATURE_ORIGINAL_REPORT)


def envelope(msg_type: str, data: BaseModel | dict[str, Any]) -> dict[str, Any]:
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    return {"type": msg_type, "data": payload}
