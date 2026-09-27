"""Job lifecycle on the server side: creation, events, planning and recording results."""

from __future__ import annotations

import logging
import os
import posixpath
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.codecs import CONTAINER_EXTENSIONS, Container, codec_label
from frameforge_shared.media import MediaInfo
from frameforge_shared.profile import ProfileSpec
from frameforge_shared.protocol import FinalizePlan, JobResult, ValidationThresholds

from ..config import get_settings
from ..db.models import ACTIVE_JOB_STATES, Job, JobEvent, Library, MediaFile, MediaMetadata, ProcessedFingerprint, Profile, Rule, StatsDaily
from ..db.types import utcnow
from ..events import bus
from . import retention
from .storage_policy import original_stays_in_library, storage_of

log = logging.getLogger(__name__)

TMP_DIR_NAME = ".frameforge-tmp"
DEFAULT_BACKUP_DIR_NAME = ".frameforge-originals"
TRANSIENT_FAILURES = {"nvenc_session_limit", "out_of_memory", "hw_filter", "node_lost", "recovered_untouched", "recovered_rollback"}


# ---------------------------------------------------------------------------
# Serialization helpers (shared by API and events)
# ---------------------------------------------------------------------------


def job_summary(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "file_id": job.file_id,
        "library_id": job.library_id,
        "filename": posixpath.basename(job.source_path),
        "source_path": job.source_path,
        "profile_id": job.profile_id,
        "profile_name": job.profile_name,
        "rule_name": job.rule_name,
        "priority": job.priority,
        "manual": job.manual,
        "state": job.state,
        "waiting_reason": job.waiting_reason,
        "node_id": job.node_id,
        "node_name": job.node_name,
        "encoder": job.encoder,
        "backend": job.backend,
        "hw_decode": job.hw_decode,
        "attempts": job.attempts,
        "source_codec": job.source_codec,
        "target_codec": job.target_codec,
        "resolution": job.resolution,
        "source_duration": job.source_duration,
        "source_size": job.source_size,
        "output_size": job.output_size,
        "output_path": job.output_path,
        "progress": job.progress,
        "fps": job.fps,
        "speed": job.speed,
        "eta_seconds": job.eta_seconds,
        "error_code": job.error_code,
        "error_title": (job.diagnosis or {}).get("title"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "assigned_at": job.assigned_at.isoformat() if job.assigned_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def publish_job(job: Job, topic: str = "job.updated") -> None:
    bus.publish(topic, job_summary(job))


async def add_event(db: AsyncSession, job_id: int, kind: str, message: str, level: str = "info", data: dict[str, Any] | None = None) -> None:
    db.add(JobEvent(job_id=job_id, kind=kind, message=message, level=level, data=data or {}))


def job_log_path(job_id: int) -> Path:
    return get_settings().job_logs_dir / f"{job_id}.log"


def append_job_log(job_id: int, lines: list[str]) -> None:
    if not lines:
        return
    with job_log_path(job_id).open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


async def active_job_for_file(db: AsyncSession, file_id: int) -> Job | None:
    q = select(Job).where(Job.file_id == file_id, Job.state.in_(("queued", *ACTIVE_JOB_STATES))).limit(1)
    return (await db.execute(q)).scalar_one_or_none()


async def create_job(
    db: AsyncSession,
    file: MediaFile,
    profile: Profile,
    *,
    rule: Rule | None = None,
    priority: int = 2,
    manual: bool = False,
) -> Job:
    spec = ProfileSpec.model_validate(profile.spec)
    info = MediaInfo.model_validate(file.meta.info) if file.meta and file.meta.info else None
    job = Job(
        file_id=file.id,
        library_id=file.library_id,
        source_path=file.path,
        profile_id=profile.id,
        profile_name=profile.name,
        profile_spec=spec.model_dump(mode="json"),
        rule_id=rule.id if rule else None,
        rule_name=rule.name if rule else None,
        schedule=dict(rule.schedule or {}) if rule else {},
        priority=priority,
        manual=manual,
        state="queued",
        source_codec=file.meta.video_codec if file.meta else None,
        target_codec="copy" if spec.is_remux else spec.video_codec.value,
        resolution=info.resolution_label() if info else None,
        source_duration=file.meta.duration if file.meta else None,
        source_size=file.size,
    )
    db.add(job)
    await db.flush()
    file.status = "queued"
    file.last_job_id = job.id
    origin = "manually" if manual else (f"by rule “{rule.name}”" if rule else "automatically")
    await add_event(
        db,
        job.id,
        "created",
        f"Queued {origin}: {codec_label(job.source_codec)} → {spec.target_label()} using “{profile.name}”",
    )
    return job


# ---------------------------------------------------------------------------
# Finalize planning
# ---------------------------------------------------------------------------


def _library_root_for(library: Library, path: str) -> str | None:
    best = None
    for root in library.paths or []:
        r = root.rstrip("/")
        if path == r or path.startswith(r + "/"):
            if best is None or len(r) > len(best):
                best = r
    return best


def backup_root_for(library: Library, src: str) -> str:
    """The backup folder for a source: the library's, or a hidden one at the root the source belongs to."""
    root = _library_root_for(library, src) or posixpath.dirname(src)
    return library.backup_path or posixpath.join(root, DEFAULT_BACKUP_DIR_NAME)


def build_plan(job: Job, library: Library, spec: ProfileSpec) -> FinalizePlan:
    src = job.source_path
    src_dir, name = posixpath.split(src)
    stem, _ = posixpath.splitext(name)
    ext = CONTAINER_EXTENSIONS[Container(spec.container)]
    root = _library_root_for(library, src) or src_dir
    rel_dir = posixpath.relpath(src_dir, root) if src_dir != root else ""
    policy = storage_of(library)
    backup: str | None = None

    if policy.output_location == "folder" and library.output_path:
        final = posixpath.join(library.output_path, rel_dir, stem + ext) if rel_dir else posixpath.join(library.output_path, stem + ext)
        action = "delete" if policy.deletes_immediately else "keep"
    elif policy.deletes_immediately:
        final = posixpath.join(src_dir, stem + ext)
        action = "delete"
    elif policy.kept_original_location == "in_place":
        final = posixpath.join(src_dir, f"{stem}.ff{ext}")  # can't collide with the source, whatever its extension
        action = "keep"
    else:  # keep the original in a backup folder; the output takes its place (default & safest in-place option)
        final = posixpath.join(src_dir, stem + ext)
        backup_root = backup_root_for(library, src)
        backup = posixpath.join(backup_root, rel_dir, name) if rel_dir else posixpath.join(backup_root, name)
        action = "backup"

    temp = posixpath.join(posixpath.dirname(final), TMP_DIR_NAME, f"job-{job.id}{ext}")
    return FinalizePlan(source=src, temp_output=temp, final_output=final, original_action=action, backup_path=backup)


def thresholds_for(library: Library | None) -> ValidationThresholds:
    return ValidationThresholds.model_validate((library.validation if library else None) or {})


def frameforge_tag(job: Job) -> str:
    return f"job={job.id};profile={job.profile_id};v=1"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


async def _bump_stats(db: AsyncSession, *, completed: int = 0, failed: int = 0, bytes_in: int = 0, bytes_out: int = 0, encode_s: float = 0, media_s: float = 0) -> None:
    today: date = datetime.now().date()
    row = (await db.execute(select(StatsDaily).where(StatsDaily.day == today))).scalar_one_or_none()
    if row is None:
        row = StatsDaily(day=today, jobs_completed=0, jobs_failed=0, bytes_in=0, bytes_out=0, encode_seconds=0.0, media_seconds=0.0)
        db.add(row)
    row.jobs_completed += completed
    row.jobs_failed += failed
    row.bytes_in += bytes_in
    row.bytes_out += bytes_out
    row.encode_seconds += encode_s
    row.media_seconds += media_s


def _apply_media(file: MediaFile, info: MediaInfo) -> None:
    from .scanner import metadata_from_info  # local import: scanner imports this module

    if file.meta is None:
        file.meta = metadata_from_info(file.id, info)
    else:
        fresh = metadata_from_info(file.id, info)
        for col in MediaMetadata.__table__.columns.keys():
            if col != "file_id":
                setattr(file.meta, col, getattr(fresh, col))


async def requeue(db: AsyncSession, job: Job, reason: str, *, exclude_node: bool = False, count_attempt: bool = True) -> None:
    if exclude_node and job.node_id is not None:
        job.excluded_nodes = sorted(set(job.excluded_nodes or []) | {job.node_id})
    if count_attempt:
        job.attempts += 1
    job.state = "queued"
    job.node_id = None
    job.node_name = None
    job.progress = 0.0
    job.fps = job.speed = job.eta_seconds = None
    job.assigned_at = job.started_at = None
    job.waiting_reason = reason
    await add_event(db, job.id, "requeued", reason, level="warn")


async def record_result(db: AsyncSession, job: Job, result: JobResult, max_attempts: int) -> None:
    file = await db.get(MediaFile, job.file_id) if job.file_id else None
    source_fingerprint = file.fingerprint if file is not None else None  # before it may become the output's
    now = utcnow()
    for note in result.notes:
        await add_event(db, job.id, "note", note, level="warn")
    job.notes = list(result.notes)

    if result.status == "rejected":
        await requeue(db, job, result.reject_reason or "Node declined the job", exclude_node=True, count_attempt=False)
        return

    job.finished_at = now
    job.encoder = result.encoder_used or job.encoder
    if result.hw_decode_used is not None:
        job.hw_decode = result.hw_decode_used
    if result.validation:
        job.validation = result.validation.model_dump(mode="json")

    if result.status == "completed":
        job.state = "completed"
        job.progress = 100.0
        job.eta_seconds = 0
        job.output_size = result.output_size
        job.output_path = result.final_path
        job.diagnosis = {}
        saved = (result.source_size or job.source_size or 0) - (result.output_size or 0)
        await add_event(db, job.id, "completed", f"Done. Output {result.final_path}", data={"saved_bytes": saved})
        await _bump_stats(
            db,
            completed=1,
            bytes_in=result.source_size or job.source_size or 0,
            bytes_out=result.output_size or 0,
            encode_s=result.duration_seconds or 0,
            media_s=job.source_duration or 0,
        )
        library = await db.get(Library, job.library_id) if job.library_id else None
        await retention.relink_successors(db, job, result, source_fingerprint)
        entry = await retention.create_entry(db, job, file, library, result, source_fingerprint)
        if entry is not None:
            when = entry.due_at.strftime("%Y-%m-%d")
            if entry.state == "blocked":
                await add_event(db, job.id, "retention", f"The original is kept: {entry.reason}", level="warn")
            else:
                await add_event(db, job.id, "retention", f"The original is kept until {when}, then deleted after the output is checked again")
        if file is not None:
            await _update_file_after_success(db, file, job, result, library)
    elif result.status == "cancelled":
        job.state = "cancelled"
        await add_event(db, job.id, "cancelled", "Cancelled. The source file was not modified.")
        if file is not None:
            file.status = "ready"
    else:
        diag = result.diagnosis
        job.diagnosis = diag.model_dump(mode="json") if diag else {}
        job.error_code = diag.code if diag else "unknown"
        if diag and diag.code in TRANSIENT_FAILURES and job.attempts + 1 < max_attempts:
            await add_event(db, job.id, "failed", f"{diag.title}. Retrying automatically.", level="warn")
            await requeue(db, job, f"Retrying after: {diag.title}", exclude_node=diag.code != "node_lost")
            job.finished_at = None
            return
        job.state = "failed"
        await add_event(db, job.id, "failed", diag.title if diag else "Failed", level="error", data={"code": job.error_code})
        await _bump_stats(db, failed=1)
        if file is not None:
            file.status = "failed"


async def _update_file_after_success(db: AsyncSession, file: MediaFile, job: Job, result: JobResult, library: Library | None = None) -> None:
    plan = FinalizePlan.model_validate(job.finalize_plan) if job.finalize_plan else None
    if file.original_size is None:
        file.original_size = job.source_size or file.size
    file.processed_profile_id = job.profile_id
    file.processed_at = utcnow()
    file.status = "processed"
    file.decision = f"Processed with “{job.profile_name}”"

    if file.fingerprint:
        db.add(ProcessedFingerprint(fingerprint=file.fingerprint, kind="original", job_id=job.id, profile_id=job.profile_id))
    if result.output_fingerprint:
        db.add(ProcessedFingerprint(fingerprint=result.output_fingerprint, kind="output", job_id=job.id, profile_id=job.profile_id))

    if plan is not None and plan.original_action == "keep" and library is not None and original_stays_in_library(storage_of(library)):
        file.role = "kept_original"  # its converted copy carries on; rules leave this one alone

    replaced = plan is not None and plan.original_action in ("delete", "backup")
    if replaced and result.final_path:
        # The library file IS the output now.
        if result.final_path != file.path:
            clash = (await db.execute(select(MediaFile).where(MediaFile.path == result.final_path))).scalar_one_or_none()
            if clash is not None and clash.id != file.id:
                await db.delete(clash)
                await db.flush()
            file.path = result.final_path
            file.filename = posixpath.basename(result.final_path)
            file.extension = posixpath.splitext(result.final_path)[1].lower()
            lib_root = _library_root_for(file.library, result.final_path) if file.library else None
            file.relative_path = posixpath.relpath(result.final_path, lib_root) if lib_root else file.filename
        file.size = result.output_size or file.size
        file.fingerprint = result.output_fingerprint
        if result.output_media:
            _apply_media(file, result.output_media)
    bus.publish("file.updated", {"id": file.id, "status": file.status})


def cleanup_job_log(job_id: int) -> None:
    try:
        os.remove(job_log_path(job_id))
    except FileNotFoundError:
        pass
