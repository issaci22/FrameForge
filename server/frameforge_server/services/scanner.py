"""Library scanning: discover files, fingerprint, probe, then let the rule engine create jobs."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import posixpath
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.codecs import VIDEO_EXTENSIONS
from frameforge_shared.fingerprint import fingerprint_file
from frameforge_shared.media import MediaInfo
from frameforge_shared.probe import ProbeError, probe_file

from ..db.models import ACTIVE_JOB_STATES, Job, Library, MediaFile, MediaMetadata, ProcessedFingerprint, Rule
from ..db.session import sessionmaker
from ..db.types import utcnow
from ..events import bus
from .jobs import DEFAULT_BACKUP_DIR_NAME, TMP_DIR_NAME, active_job_for_file, create_job, publish_job
from .rules.conditions import HardwareSnapshot
from .rules.engine import build_context, decide
from .system_settings import load_settings

log = logging.getLogger(__name__)

# Files modified more recently than this are assumed to still be recording/copying.
STABLE_SECONDS = 120
_COMMIT_EVERY = 100


@dataclass
class FoundFile:
    path: str
    relative: str
    size: int
    mtime: float


_locks: dict[int, asyncio.Lock] = {}
_scan_state: dict[int, dict[str, Any]] = {}


def scan_state(library_id: int) -> dict[str, Any] | None:
    return _scan_state.get(library_id)


def is_scanning(library_id: int) -> bool:
    lock = _locks.get(library_id)
    return bool(lock and lock.locked())


def _publish_scan(library_id: int, **data: Any) -> None:
    state = _scan_state.setdefault(library_id, {})
    state.update(data)
    bus.publish("library.scan", {"library_id": library_id, **state})


def metadata_from_info(file_id: int, info: MediaInfo) -> MediaMetadata:
    v = info.video
    return MediaMetadata(
        file_id=file_id,
        container=info.container,
        duration=info.duration,
        bitrate=info.bitrate,
        video_codec=v.codec if v else None,
        width=v.display_width if v else None,
        height=v.display_height if v else None,
        short_side=min(v.width, v.height) if v else None,
        fps=v.fps if v else None,
        bit_depth=v.bit_depth if v else None,
        hdr_format=v.hdr_format if v else None,
        audio_codecs=[a.codec for a in info.audio],
        audio_count=len(info.audio),
        subtitle_count=len(info.subtitles),
        chapter_count=info.chapter_count,
        creation_time=info.creation_time,
        frameforge_tag=info.frameforge_tag,
        info=info.model_dump(mode="json"),
        probed_at=utcnow(),
    )


def _excluded_dirs(library: Library) -> list[str]:
    out = [p.rstrip("/") for p in (library.output_path, library.backup_path) if p]
    return out


def walk_library(library: Library) -> tuple[list[FoundFile], list[str]]:
    """Blocking directory walk. Returns (files, errors)."""
    found: list[FoundFile] = []
    errors: list[str] = []
    skip_dirs = _excluded_dirs(library)
    patterns = [p for p in (library.exclude_patterns or []) if p]
    for root in library.paths or []:
        root = root.rstrip("/") or "/"
        if not os.path.isdir(root):
            errors.append(f"Folder not found or not mounted: {root}")
            continue

        def onerror(err: OSError) -> None:
            errors.append(f"{err.filename}: {err.strerror}")

        for dirpath, dirnames, filenames in os.walk(root, onerror=onerror, followlinks=False):
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d not in (TMP_DIR_NAME, DEFAULT_BACKUP_DIR_NAME) and posixpath.join(dirpath, d) not in skip_dirs
            ]
            for name in filenames:
                if name.startswith("."):
                    continue
                ext = posixpath.splitext(name)[1].lower()
                if ext not in VIDEO_EXTENSIONS:
                    continue
                full = posixpath.join(dirpath, name)
                rel = posixpath.relpath(full, root)
                if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p) for p in patterns):
                    continue
                try:
                    st = os.stat(full)
                except OSError as exc:
                    errors.append(f"{full}: {exc.strerror}")
                    continue
                found.append(FoundFile(full, rel, st.st_size, st.st_mtime))
    return found, errors


async def scan_library(library_id: int, reason: str = "manual") -> dict[str, Any]:
    lock = _locks.setdefault(library_id, asyncio.Lock())
    if lock.locked():
        return {"status": "already_running"}
    async with lock:
        started = time.monotonic()
        _scan_state[library_id] = {}
        _publish_scan(library_id, phase="walking", message="Looking for video files…", discovered=0, probed=0, to_probe=0, started_at=utcnow().isoformat())
        try:
            summary = await _scan(library_id)
        except Exception as exc:  # keep the loop alive; surface the error in the UI
            log.exception("Scan of library %s failed", library_id)
            summary = {"error": str(exc)}
        summary["duration_s"] = round(time.monotonic() - started, 1)
        summary["reason"] = reason
        async with sessionmaker()() as db:
            lib = await db.get(Library, library_id)
            if lib is not None:
                lib.last_scan_at = utcnow()
                lib.last_scan_summary = summary
                await db.commit()
        _publish_scan(library_id, phase="done", message="Scan complete", summary=summary)
        log.info("Scan of library %s finished: %s", library_id, summary)
        return summary


async def _scan(library_id: int) -> dict[str, Any]:
    async with sessionmaker()() as db:
        library = await db.get(Library, library_id)
        if library is None:
            return {"error": "Library not found"}
        found, errors = await asyncio.to_thread(walk_library, library)
        _publish_scan(library_id, phase="comparing", discovered=len(found), message=f"Found {len(found)} video files")

        existing = {f.path: f for f in (await db.execute(select(MediaFile).where(MediaFile.library_id == library_id))).scalars()}
        now_ts = time.time()
        seen: set[str] = set()
        to_probe: list[tuple[MediaFile, FoundFile, bool]] = []
        new_count = changed = unstable = 0

        for ff in found:
            seen.add(ff.path)
            if now_ts - ff.mtime < STABLE_SECONDS:
                unstable += 1
                continue
            mtime = datetime.fromtimestamp(ff.mtime, UTC)
            row = existing.get(ff.path)
            if row is None:
                row = MediaFile(
                    library_id=library_id,
                    path=ff.path,
                    relative_path=ff.relative,
                    filename=posixpath.basename(ff.path),
                    extension=posixpath.splitext(ff.path)[1].lower(),
                    size=ff.size,
                    mtime=mtime,
                    status="new",
                    meta=None,
                )
                db.add(row)
                new_count += 1
                to_probe.append((row, ff, True))
            else:
                row.last_seen_at = utcnow()
                if row.status == "missing":
                    row.status = "ready" if row.meta else "new"
                if row.size != ff.size or abs(row.mtime.timestamp() - ff.mtime) > 1:
                    if row.status in ("queued", "processing"):
                        continue  # never touch a file mid-job; the job revalidates it
                    row.size = ff.size
                    row.mtime = mtime
                    row.status = "new"
                    row.role = "source"  # different content: a new file as far as rules are concerned
                    changed += 1
                    to_probe.append((row, ff, False))
                elif row.status == "new" and not row.meta:
                    to_probe.append((row, ff, False))

        missing = 0
        for path, row in existing.items():
            if path not in seen and row.status not in ("missing", "queued", "processing"):
                row.status = "missing"
                missing += 1
        await db.commit()

        _publish_scan(library_id, phase="analyzing", to_probe=len(to_probe), probed=0, message=f"Analyzing {len(to_probe)} files")
        moved = await _probe_all(db, library_id, to_probe)
        created = await evaluate_library(db, library_id)

    return {
        "found": len(found),
        "new": new_count,
        "changed": changed,
        "missing": missing,
        "moved": moved,
        "still_recording": unstable,
        "analyzed": len(to_probe),
        "jobs_created": created,
        "errors": errors[:20],
    }


async def _probe_all(db: AsyncSession, library_id: int, items: list[tuple[MediaFile, FoundFile, bool]]) -> int:
    settings = await load_settings(db)
    sem = asyncio.Semaphore(settings.probe_concurrency)
    done = 0
    moved = 0

    async def work(ff: FoundFile) -> tuple[str | None, MediaInfo | None, str | None]:
        async with sem:
            try:
                fp = await asyncio.to_thread(fingerprint_file, ff.path)
            except OSError as exc:
                return None, None, f"Can't read file: {exc.strerror}"
            try:
                return fp, await probe_file(ff.path), None
            except ProbeError as exc:
                return fp, None, str(exc)

    for start in range(0, len(items), _COMMIT_EVERY):
        batch = items[start : start + _COMMIT_EVERY]
        results = await asyncio.gather(*(work(ff) for _, ff, _ in batch))
        for (row, _ff, is_new), (fp, info, err) in zip(batch, results):
            row.fingerprint = fp
            if fp and is_new:
                moved += await _adopt_moved(db, library_id, row, fp)
            await db.flush()
            if err or info is None:
                row.status = "error"
                row.probe_error = err
                continue
            row.probe_error = None
            meta = metadata_from_info(row.id, info)
            if row.meta is None:
                row.meta = meta
            else:
                for col in MediaMetadata.__table__.columns.keys():
                    if col != "file_id":
                        setattr(row.meta, col, getattr(meta, col))
            if row.status in ("new", "error"):
                row.status = "ready"
            await _apply_processed_fingerprint(db, row)
        await db.commit()
        done += len(batch)
        _publish_scan(library_id, probed=done, message=f"Analyzed {done} of {len(items)} files")
    return moved


async def _adopt_moved(db: AsyncSession, library_id: int, row: MediaFile, fingerprint: str) -> int:
    """If a 'missing' file has the same fingerprint, this is a move: carry its history over."""
    q = select(MediaFile).where(MediaFile.fingerprint == fingerprint, MediaFile.status == "missing", MediaFile.library_id == library_id).limit(1)
    old = (await db.execute(q)).scalar_one_or_none()
    if old is None:
        return 0
    row.original_size = old.original_size
    row.processed_profile_id = old.processed_profile_id
    row.processed_at = old.processed_at
    row.last_job_id = old.last_job_id
    row.ignored = old.ignored
    row.role = old.role
    await db.delete(old)
    return 1


async def _apply_processed_fingerprint(db: AsyncSession, row: MediaFile) -> None:
    """Outputs FrameForge produced are recognized even after being moved/renamed."""
    if row.processed_profile_id or not row.fingerprint:
        return
    # Outputs we produced, and originals we already handled (e.g. a rescued ".original" copy).
    q = select(ProcessedFingerprint).where(ProcessedFingerprint.fingerprint == row.fingerprint).order_by(ProcessedFingerprint.id.desc()).limit(1)
    hit = (await db.execute(q)).scalar_one_or_none()
    if hit is not None:
        row.processed_profile_id = hit.profile_id
        row.status = "processed"
        if hit.kind == "original":
            row.role = "kept_original"  # e.g. an original restored from a backup folder
        return
    tag = row.meta.frameforge_tag if row.meta else None
    if tag:
        for part in tag.split(";"):
            if part.startswith("profile="):
                try:
                    row.processed_profile_id = int(part.split("=", 1)[1])
                    row.status = "processed"
                except ValueError:
                    pass


async def hardware_snapshot() -> HardwareSnapshot:
    from .node_manager import manager  # avoid import cycle

    return manager.hardware_snapshot()


async def evaluate_library(db: AsyncSession, library_id: int) -> int:
    """Run the rule engine over every file in the library and queue jobs. Returns jobs created."""
    library = await db.get(Library, library_id)
    if library is None or not library.enabled:
        return 0
    rules = list((await db.execute(select(Rule).where((Rule.library_id == library_id) | (Rule.library_id.is_(None))))).scalars())
    hw = await hardware_snapshot()
    active_file_ids = set(
        (await db.execute(select(Job.file_id).where(Job.library_id == library_id, Job.state.in_(("queued", *ACTIVE_JOB_STATES))))).scalars()
    )
    failed: dict[int, set[int]] = {}
    for file_id, profile_id in (await db.execute(select(Job.file_id, Job.profile_id).where(Job.library_id == library_id, Job.state == "failed"))).all():
        if file_id is not None and profile_id is not None:
            failed.setdefault(file_id, set()).add(profile_id)

    files = (await db.execute(select(MediaFile).where(MediaFile.library_id == library_id))).scalars().all()
    created = 0
    new_jobs = []
    for f in files:
        decision = decide(f, rules, build_context(f, hw), has_active_job=f.id in active_file_ids, failed_profile_ids=failed.get(f.id))
        if f.status not in ("queued", "processing"):
            f.decision = decision.reason[:255]
        if decision.action != "transcode" or not library.automation_enabled:
            if decision.action == "transcode":
                f.decision = f"Would transcode ({decision.reason}), but automation is off for this library"[:255]
            continue
        if await active_job_for_file(db, f.id):
            continue
        job = await create_job(db, f, decision.profile, rule=decision.rule, priority=decision.priority)  # type: ignore[arg-type]
        new_jobs.append(job)
        created += 1
    await db.commit()
    for job in new_jobs:
        publish_job(job, "job.created")
    if created:
        from .scheduler import scheduler

        scheduler.wake()
    return created


async def evaluate_all_libraries() -> int:
    total = 0
    async with sessionmaker()() as db:
        ids = list((await db.execute(select(Library.id).where(Library.enabled.is_(True)))).scalars())
        for lib_id in ids:
            if not is_scanning(lib_id):
                total += await evaluate_library(db, lib_id)
    return total


async def scan_loop(stop: asyncio.Event) -> None:
    """Periodic scans per library interval."""
    await asyncio.sleep(10)
    while not stop.is_set():
        try:
            async with sessionmaker()() as db:
                libs = list((await db.execute(select(Library).where(Library.enabled.is_(True)))).scalars())
            now = utcnow()
            for lib in libs:
                interval = max(5, lib.scan_interval_minutes or 60) * 60
                if lib.last_scan_at is None or (now - lib.last_scan_at).total_seconds() >= interval:
                    asyncio.create_task(scan_library(lib.id, reason="scheduled"))
        except Exception:
            log.exception("Scan scheduler error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
