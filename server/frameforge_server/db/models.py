"""SQLAlchemy ORM models. Schema changes REQUIRE an Alembic migration."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, Date, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .types import UTCDateTime, utcnow


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(lazy="joined")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Libraries & files
# ---------------------------------------------------------------------------


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    paths: Mapped[list[Any]] = mapped_column(default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    exclude_patterns: Mapped[list[Any]] = mapped_column(default=list)
    # Legacy single value (replace | backup | output_dir | alongside), derived from the fields below.
    # Kept so older clients and a downgrade still work; see services/storage_policy.py.
    output_policy: Mapped[str] = mapped_column(String(32), default="backup")
    output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    backup_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    output_location: Mapped[str] = mapped_column(String(16), default="source_folder")  # source_folder | folder
    original_handling: Mapped[str] = mapped_column(String(16), default="keep")  # keep | delete | keep_days
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kept_original_location: Mapped[str] = mapped_column(String(16), default="backup")  # backup | in_place
    validation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_scan_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_scan_summary: Mapped[dict[str, Any]] = mapped_column(default=dict)


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (Index("ix_media_files_library_status", "library_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(2048), unique=True)
    relative_path: Mapped[str] = mapped_column(String(2048))
    filename: Mapped[str] = mapped_column(String(512))
    extension: Mapped[str] = mapped_column(String(16))
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[datetime]
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # new | ready | queued | processing | processed | failed | error | missing
    status: Mapped[str] = mapped_column(String(16), default="new")
    ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    probe_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    original_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processed_profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(255), nullable=True)  # last rule-engine outcome, human readable
    # source | kept_original (an original that stayed in the library after its conversion; rules leave it alone)
    role: Mapped[str] = mapped_column(String(16), default="source")

    meta: Mapped[MediaMetadata | None] = relationship(back_populates="file", uselist=False, cascade="all, delete-orphan", lazy="selectin")
    library: Mapped[Library] = relationship(lazy="joined")


class MediaMetadata(Base):
    __tablename__ = "media_metadata"

    file_id: Mapped[int] = mapped_column(ForeignKey("media_files.id", ondelete="CASCADE"), primary_key=True)
    container: Mapped[str] = mapped_column(String(32))
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    bitrate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    short_side: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    bit_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hdr_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_codecs: Mapped[list[Any]] = mapped_column(default=list)
    audio_count: Mapped[int] = mapped_column(Integer, default=0)
    subtitle_count: Mapped[int] = mapped_column(Integer, default=0)
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)
    creation_time: Mapped[datetime | None] = mapped_column(nullable=True)
    frameforge_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)
    info: Mapped[dict[str, Any]] = mapped_column(default=dict)  # full MediaInfo document
    probed_at: Mapped[datetime] = mapped_column(default=utcnow)

    file: Mapped[MediaFile] = relationship(back_populates="meta")


class ProcessedFingerprint(Base):
    """Fingerprints of files FrameForge produced or already handled, so moved files aren't redone."""

    __tablename__ = "processed_fingerprints"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # output | original
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------------------
# Profiles & rules
# ---------------------------------------------------------------------------


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    builtin_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    based_on: Mapped[str | None] = mapped_column(String(64), nullable=True)  # built-in key the user started from
    spec: Mapped[dict[str, Any]] = mapped_column(default=dict)  # ProfileSpec document
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), nullable=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    conditions: Mapped[dict[str, Any]] = mapped_column(default=dict)  # condition tree document
    action: Mapped[str] = mapped_column(String(16), default="transcode")  # transcode | skip
    profile_id: Mapped[int | None] = mapped_column(ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    schedule: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"window": {"start": "23:00", "end": "07:00"}, "days": [...]}
    skip_if_target_codec: Mapped[bool] = mapped_column(Boolean, default=True)
    policy_group: Mapped[str | None] = mapped_column(String(64), nullable=True)  # set when generated by an aging policy
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    profile: Mapped[Profile | None] = relationship(lazy="joined")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    token_hint: Mapped[str] = mapped_column(String(16))
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    reserve_slot_for_normal: Mapped[bool] = mapped_column(Boolean, default=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"max_gpu_util": 70, "max_cpu_util": 90, "window": {...}}
    path_mappings: Mapped[list[Any]] = mapped_column(default=list)
    hardware_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)  # cpu | nvidia | intel | amd (wizard choice)
    capabilities: Mapped[dict[str, Any]] = mapped_column(default=dict)  # NodeCapabilities document
    last_metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(nullable=True)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

ACTIVE_JOB_STATES = ("assigned", "preparing", "transcoding", "validating", "finalizing")
TERMINAL_JOB_STATES = ("completed", "failed", "cancelled")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_state_priority", "state", "priority"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("media_files.id", ondelete="SET NULL"), index=True, nullable=True)
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True)
    source_path: Mapped[str] = mapped_column(String(2048))
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_name: Mapped[str] = mapped_column(String(128))
    profile_spec: Mapped[dict[str, Any]] = mapped_column(default=dict)
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schedule: Mapped[dict[str, Any]] = mapped_column(default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    # queued | assigned | preparing | transcoding | validating | finalizing | completed | failed | cancelled
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    waiting_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    node_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="SET NULL"), nullable=True, index=True)
    node_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    excluded_nodes: Mapped[list[Any]] = mapped_column(default=list)
    encoder: Mapped[str | None] = mapped_column(String(32), nullable=True)
    backend: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hw_decode: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    source_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    finalize_plan: Mapped[dict[str, Any]] = mapped_column(default=dict)

    progress: Mapped[float] = mapped_column(Float, default=0.0)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    eta_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diagnosis: Mapped[dict[str, Any]] = mapped_column(default=dict)
    validation: Mapped[dict[str, Any]] = mapped_column(default=dict)
    notes: Mapped[list[Any]] = mapped_column(default=list)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    assigned_at: Mapped[datetime | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class RetainedOriginal(Base):
    """An original kept for a while after a verified conversion, then deleted by services/retention.py.

    Self-contained on purpose: job history is purged after a while, but these rows must still prove,
    at deletion time, that the output is intact and the original is the file that was converted.
    """

    __tablename__ = "retained_originals"
    __table_args__ = (Index("ix_retained_originals_state_due", "state", "due_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int | None] = mapped_column(ForeignKey("libraries.id", ondelete="SET NULL"), nullable=True, index=True)
    job_id: Mapped[int] = mapped_column(Integer)  # the conversion that produced the output
    output_job_id: Mapped[int] = mapped_column(Integer)  # the job whose FRAMEFORGE tag the output carries now
    media_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the original's row, when it stays in the library
    original_path: Mapped[str] = mapped_column(String(2048))
    allowed_root: Mapped[str] = mapped_column(String(2048))  # the original must be inside this folder to be deleted
    original_size: Mapped[int] = mapped_column(BigInteger)
    original_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_path: Mapped[str] = mapped_column(String(2048))
    output_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    due_at: Mapped[datetime]
    # pending | blocked | deleted | kept | gone
    state: Mapped[str] = mapped_column(String(16), default="pending")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(default=utcnow)
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | warn | error
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class StatsDaily(Base):
    __tablename__ = "stats_daily"
    __table_args__ = (UniqueConstraint("day", name="uq_stats_daily_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    jobs_completed: Mapped[int] = mapped_column(Integer, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, default=0)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0)
    encode_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    media_seconds: Mapped[float] = mapped_column(Float, default=0.0)
