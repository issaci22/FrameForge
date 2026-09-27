"""Timed deletion of originals kept after a verified conversion ("keep for N days").

Besides the node finalizer, this is the ONLY code allowed to delete source media (AGENTS.md §3).
It deletes an original only when every check below passes at deletion time:

* the library still exists and still deletes originals after a period (manual "delete now" excepted);
* the original exists, is a regular file (not a symlink), sits inside ``allowed_root`` and still has
  the size and fingerprint recorded when it was converted;
* no queued or running job uses the original or the output;
* the output exists (found by fingerprint if it was moved inside the library), has the recorded size
  and fingerprint, probes cleanly, has a video stream, carries ``job=<output_job_id>;`` in its
  FRAMEFORGE tag, matches the original's duration, and its start and end decode cleanly.

Anything else leaves the file alone and records why (``blocked``). Blocked entries are re-checked on
every pass, because many reasons (an unmounted share, a busy file) are temporary.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import posixpath
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.fingerprint import fingerprint_file
from frameforge_shared.probe import ProbeError, decode_check, probe_file
from frameforge_shared.protocol import FinalizePlan, JobResult, ValidationThresholds

from ..db.models import ACTIVE_JOB_STATES, Job, Library, MediaFile, RetainedOriginal
from ..db.session import sessionmaker
from ..db.types import utcnow
from ..events import bus
from .storage_policy import storage_of

log = logging.getLogger(__name__)

RETENTION_INTERVAL = 900  # seconds between passes
OPEN_STATES = ("pending", "blocked")
MIN_DURATION_TOLERANCE_PCT = 2.0

# One deletion at a time, for the loop and "delete now" alike. The files API checks it too.
lock = asyncio.Lock()
_deleting: set[str] = set()


def is_being_deleted(path: str) -> bool:
    return path in _deleting


@dataclass
class CheckOutcome:
    ok: bool
    reason: str | None = None
    gone: bool = False  # the original no longer exists: nothing left to do


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def entry_view(entry: RetainedOriginal, library: Library | None = None) -> dict:
    paused = None
    if entry.state in OPEN_STATES:
        if library is None:
            paused = "The library was removed"
        elif not storage_of(library).timed:
            paused = "The library no longer deletes originals after a period"
    return {
        "id": entry.id,
        "library_id": entry.library_id,
        "job_id": entry.job_id,
        "original_path": entry.original_path,
        "filename": posixpath.basename(entry.original_path),
        "original_size": entry.original_size,
        "output_path": entry.output_path,
        "output_size": entry.output_size,
        "created_at": entry.created_at.isoformat(),
        "due_at": entry.due_at.isoformat(),
        "state": entry.state,
        "reason": entry.reason,
        "paused": paused,
        "checked_at": entry.checked_at.isoformat() if entry.checked_at else None,
        "deleted_at": entry.deleted_at.isoformat() if entry.deleted_at else None,
    }


def _publish(entry: RetainedOriginal) -> None:
    bus.publish("retention.updated", {"id": entry.id, "library_id": entry.library_id, "state": entry.state})


# ---------------------------------------------------------------------------
# Creating and re-linking entries (called from record_result)
# ---------------------------------------------------------------------------


def _root_for(library: Library, path: str) -> str | None:
    best = None
    for root in library.paths or []:
        r = root.rstrip("/")
        if path == r or path.startswith(r + "/"):
            if best is None or len(r) > len(best):
                best = r
    return best


def _allowed_root(library: Library, plan: FinalizePlan) -> str:
    """The folder the original must still be inside when it is deleted."""
    from .jobs import backup_root_for  # jobs imports this module

    if plan.original_action == "backup":
        return backup_root_for(library, plan.source)
    return _root_for(library, plan.source) or posixpath.dirname(plan.source)


async def relink_successors(db: AsyncSession, job: Job, result: JobResult, source_fingerprint: str | None) -> None:
    """A later job converted an output we are guarding (e.g. an aging stage): guard the newer output instead.

    The newer output was verified against the older one, which was verified against the original, so the
    chain still proves the original is safe to delete. Its FRAMEFORGE tag now names the newer job.
    """
    if not result.final_path:
        return
    conds = [RetainedOriginal.output_path == job.source_path]
    if source_fingerprint:
        conds.append(RetainedOriginal.output_fingerprint == source_fingerprint)
    rows = (await db.execute(select(RetainedOriginal).where(RetainedOriginal.state.in_(OPEN_STATES), or_(*conds)))).scalars().all()
    for entry in rows:
        if entry.job_id == job.id:
            continue
        entry.output_path = result.final_path
        entry.output_size = result.output_size
        entry.output_fingerprint = result.output_fingerprint
        entry.output_job_id = job.id
        log.info("Retention %s now guards %s (converted again by job %s)", entry.id, result.final_path, job.id)


async def create_entry(
    db: AsyncSession, job: Job, file: MediaFile | None, library: Library | None, result: JobResult, source_fingerprint: str | None
) -> RetainedOriginal | None:
    """Record an original to delete later, if the library keeps originals for a period. Completed jobs only."""
    if result.status != "completed" or library is None or not result.final_path:
        return None
    policy = storage_of(library)
    if not policy.timed or not job.finalize_plan:
        return None
    plan = FinalizePlan.model_validate(job.finalize_plan)
    if plan.original_action == "delete":
        return None

    disposition = result.original_disposition
    state, reason = "pending", None
    if disposition is None:  # node from before original reporting: rely on the plan and the fingerprint
        path = plan.backup_path if plan.original_action == "backup" else plan.source
    elif disposition in ("backed_up", "kept"):
        path = result.original_path
    elif disposition == "deleted":
        return None
    else:  # rescued / unknown: the original isn't where we planned it; a person should look
        path = result.original_path or plan.source
        state, reason = "blocked", "The original didn't end up where planned. Check it and delete it yourself."
    if not path:
        return None

    now = utcnow()
    entry = RetainedOriginal(
        library_id=library.id,
        job_id=job.id,
        output_job_id=job.id,
        media_file_id=file.id if file is not None and plan.original_action == "keep" else None,
        original_path=path,
        allowed_root=_allowed_root(library, plan),
        original_size=result.original_size or job.source_size or 0,
        original_fingerprint=result.original_fingerprint or source_fingerprint,
        original_duration=job.source_duration,
        output_path=result.final_path,
        output_size=result.output_size,
        output_fingerprint=result.output_fingerprint,
        created_at=now,
        due_at=now + timedelta(days=policy.retention_days or 0),
        state=state,
        reason=reason,
    )
    db.add(entry)
    return entry


async def on_retention_days_changed(db: AsyncSession, library: Library, old_days: int | None) -> None:
    """A longer period pushes pending deletions back. A shorter one never brings them forward by itself."""
    policy = storage_of(library)
    if not policy.timed or (old_days and policy.retention_days and policy.retention_days <= old_days):
        return
    rows = (await db.execute(select(RetainedOriginal).where(RetainedOriginal.library_id == library.id, RetainedOriginal.state.in_(OPEN_STATES)))).scalars().all()
    for entry in rows:
        later = entry.created_at + timedelta(days=policy.retention_days or 0)
        if later > entry.due_at:
            entry.due_at = later


async def apply_period(db: AsyncSession, library: Library) -> int:
    """Explicit user action: recompute pending due dates from the current period (may bring them forward)."""
    policy = storage_of(library)
    if not policy.timed:
        return 0
    rows = (await db.execute(select(RetainedOriginal).where(RetainedOriginal.library_id == library.id, RetainedOriginal.state.in_(OPEN_STATES)))).scalars().all()
    for entry in rows:
        entry.due_at = entry.created_at + timedelta(days=policy.retention_days or 0)
    return len(rows)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _inside(path: str, root: str) -> bool:
    real, base = os.path.realpath(path), os.path.realpath(root)
    return real != base and real.startswith(base.rstrip("/") + "/")


async def _find_moved_output(db: AsyncSession, entry: RetainedOriginal) -> str | None:
    if not entry.output_fingerprint or entry.library_id is None:
        return None
    q = select(MediaFile.path).where(MediaFile.library_id == entry.library_id, MediaFile.fingerprint == entry.output_fingerprint).limit(1)
    path = (await db.execute(q)).scalar_one_or_none()
    return path if path and os.path.isfile(path) else None


async def check_entry(db: AsyncSession, entry: RetainedOriginal, library: Library | None) -> CheckOutcome:
    # --- The original -----------------------------------------------------------
    original = entry.original_path
    if not os.path.lexists(original):
        return CheckOutcome(False, "The original is no longer there", gone=True)
    if os.path.islink(original) or not os.path.isfile(original):
        return CheckOutcome(False, "The original is not a regular file (e.g. a link); FrameForge won't delete it")
    if not _inside(original, entry.allowed_root):
        return CheckOutcome(False, f"The original is outside {entry.allowed_root}; FrameForge only deletes where it put or found it")
    try:
        size = os.path.getsize(original)
    except OSError as exc:
        return CheckOutcome(False, f"Can't read the original: {exc.strerror or exc}")
    if size != entry.original_size:
        return CheckOutcome(False, "The original changed after it was converted (different size)")
    if entry.original_fingerprint:
        try:
            fp = await asyncio.to_thread(fingerprint_file, original)
        except OSError as exc:
            return CheckOutcome(False, f"Can't read the original: {exc.strerror or exc}")
        if fp != entry.original_fingerprint:
            return CheckOutcome(False, "The original changed after it was converted (different content)")

    # --- Nothing may be using either file ---------------------------------------
    busy = (
        await db.execute(
            select(Job.id).where(Job.state.in_(("queued", *ACTIVE_JOB_STATES)), Job.source_path.in_((original, entry.output_path))).limit(1)
        )
    ).scalar_one_or_none()
    if busy is not None:
        return CheckOutcome(False, f"Job {busy} is using this file; waiting for it to finish")

    # --- The output -------------------------------------------------------------
    output = entry.output_path
    if not os.path.isfile(output):
        moved = await _find_moved_output(db, entry)
        if moved is None:
            return CheckOutcome(False, "The converted file is missing, so the original is kept")
        output = moved
    if os.path.realpath(output) == os.path.realpath(original):
        return CheckOutcome(False, "The converted file and the original are the same file")
    try:
        out_size = os.path.getsize(output)
    except OSError as exc:
        return CheckOutcome(False, f"Can't read the converted file: {exc.strerror or exc}")
    if entry.output_size is not None and out_size != entry.output_size:
        return CheckOutcome(False, "The converted file changed since it was verified (different size)")
    if entry.output_fingerprint:
        try:
            if await asyncio.to_thread(fingerprint_file, output) != entry.output_fingerprint:
                return CheckOutcome(False, "The converted file changed since it was verified (different content)")
        except OSError as exc:
            return CheckOutcome(False, f"Can't read the converted file: {exc.strerror or exc}")
    try:
        info = await probe_file(output)
    except ProbeError as exc:
        return CheckOutcome(False, f"The converted file can't be read: {exc}")
    if info.video is None:
        return CheckOutcome(False, "The converted file has no video stream")
    if f"job={entry.output_job_id};" not in (info.frameforge_tag or "") + ";":
        return CheckOutcome(False, "The converted file isn't the one FrameForge produced (its FRAMEFORGE tag doesn't match)")
    if entry.original_duration and entry.original_duration > 0:
        thresholds = ValidationThresholds.model_validate((library.validation if library else None) or {})
        tolerance = max(MIN_DURATION_TOLERANCE_PCT, thresholds.duration_tolerance_pct)
        diff = abs(info.duration - entry.original_duration) / entry.original_duration * 100
        if diff > tolerance:
            return CheckOutcome(False, f"The converted file's duration differs from the original's by {diff:.1f}%")
    for from_end in (False, True):
        err = await decode_check(output, from_end)
        if err:
            return CheckOutcome(False, f"The converted file doesn't decode cleanly at the {'end' if from_end else 'start'}: {err}")
    if output != entry.output_path:
        entry.output_path = output
    return CheckOutcome(True)


async def process_entry(db: AsyncSession, entry: RetainedOriginal, *, manual: bool = False) -> RetainedOriginal:
    """Check one entry and delete its original if every check passes. Caller commits. Hold ``lock``."""
    library = await db.get(Library, entry.library_id) if entry.library_id is not None else None
    if not manual and (library is None or not storage_of(library).timed):
        return entry  # paused: never delete on a policy the user has turned off
    now = utcnow()
    entry.checked_at = now
    _deleting.add(entry.original_path)
    try:
        outcome = await check_entry(db, entry, library)
        if outcome.gone:
            entry.state, entry.reason = "gone", outcome.reason
        elif not outcome.ok:
            entry.state, entry.reason = "blocked", outcome.reason
            log.warning("Kept original %s: %s", entry.original_path, outcome.reason)
        else:
            try:
                os.remove(entry.original_path)
            except OSError as exc:
                entry.state, entry.reason = "blocked", f"Couldn't delete the original: {exc.strerror or exc}"
            else:
                entry.state, entry.reason, entry.deleted_at = "deleted", ("Deleted by you" if manual else None), now
                log.info("Deleted original %s (retention entry %s, output %s verified)", entry.original_path, entry.id, entry.output_path)
                await _forget_file(db, entry)
    finally:
        _deleting.discard(entry.original_path)
    _publish(entry)
    return entry


async def _forget_file(db: AsyncSession, entry: RetainedOriginal) -> None:
    """An original that stayed in the library is gone now: drop its row instead of showing it as missing."""
    if entry.media_file_id is None:
        return
    row = await db.get(MediaFile, entry.media_file_id)
    if row is not None and row.path == entry.original_path:
        await db.delete(row)
        bus.publish("file.updated", {"id": row.id, "status": "deleted"})


async def run_due(now_limit: int | None = None) -> int:
    """One pass: check every open entry that is due. Returns how many originals were deleted."""
    deleted = 0
    async with lock, sessionmaker()() as db:
        q = select(RetainedOriginal).where(RetainedOriginal.state.in_(OPEN_STATES), RetainedOriginal.due_at <= utcnow()).order_by(RetainedOriginal.due_at)
        if now_limit:
            q = q.limit(now_limit)
        for entry in (await db.execute(q)).scalars().all():
            await process_entry(db, entry)
            deleted += entry.state == "deleted"
            await db.commit()
    return deleted


async def retention_loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(30)
    while not stop.is_set():
        try:
            deleted = await run_due()
            if deleted:
                log.info("Retention pass deleted %d original(s)", deleted)
        except Exception:
            log.exception("Retention pass failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=RETENTION_INTERVAL)
