"""Queue operations on many jobs: cancelling pending work, stopping running jobs, clearing the queue.

Cancelling a queued job is a conditional UPDATE (``WHERE state = 'queued'``), and so is the scheduler's claim of
one, so neither can overwrite the other: a job is either cancelled or started, never cancelled in the database
while a node runs it (AGENTS.md §15).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ACTIVE_JOB_STATES, Job, JobEvent, MediaFile
from ..db.types import utcnow
from ..events import bus
from . import jobs as job_service
from .node_manager import manager

log = logging.getLogger(__name__)

# Serializes job creation (rule evaluation, manual queueing, retries) with cancelling and clearing. Without it a
# clear could interleave with an evaluation, and two evaluations could both create a job for the same file.
# FrameForge runs one server process, so an in-process lock is enough.
queue_lock = asyncio.Lock()

STOPPING_REASON = "Stopping…"
CLEARED_MESSAGE = "Cancelled when you cleared the queue. The file wasn't touched."
_CHUNK = 500  # ids per IN (...) clause, well under SQLite's bound-parameter limit


def _chunks(ids: Sequence[int]) -> Iterator[Sequence[int]]:
    for start in range(0, len(ids), _CHUNK):
        yield ids[start : start + _CHUNK]


async def cancel_queued(db: AsyncSession, *, job_ids: Sequence[int] | None, message: str, by_user: bool, file_status: str = "ready") -> list[int]:
    """Cancel jobs that are still queued (every queued job when ``job_ids`` is None). Returns the ids cancelled.

    Jobs that left the queue in the meantime are skipped, never overwritten. ``by_user`` records a stop request,
    which keeps rules from queueing the same file again until the user retries it. The caller commits.
    """
    now = utcnow()
    values: dict[str, Any] = {"state": "cancelled", "finished_at": now, "waiting_reason": None}
    if by_user:
        values["cancel_requested_at"] = now
    stmt = update(Job).where(Job.state == "queued").values(**values).returning(Job.id).execution_options(synchronize_session=False)
    if job_ids is None:
        ids = list((await db.execute(stmt)).scalars())
    else:
        ids = []
        for chunk in _chunks(list(job_ids)):
            ids += list((await db.execute(stmt.where(Job.id.in_(chunk)))).scalars())
    for chunk in _chunks(ids):
        file_ids = select(Job.file_id).where(Job.id.in_(chunk), Job.file_id.is_not(None))
        await db.execute(
            update(MediaFile).where(MediaFile.id.in_(file_ids), MediaFile.status == "queued").values(status=file_status).execution_options(synchronize_session=False)
        )
    db.add_all([JobEvent(job_id=job_id, kind="cancelled", message=message, level="info", data={}) for job_id in ids])
    return ids


async def request_stop(db: AsyncSession, jobs: Sequence[Job]) -> None:
    """Ask nodes to stop running jobs. Jobs that are finalizing are left to finish. Commits.

    The stop request is saved before anything is sent, so whatever happens next (the node reports a failure,
    rejects the job or drops off) ends the job as cancelled instead of queueing it again. The job only shows as
    cancelled once the node confirms; until then it says it is stopping.
    """
    now = utcnow()
    targets: list[Job] = []
    for job in jobs:
        if job.state not in ACTIVE_JOB_STATES or job.state == "finalizing":
            continue
        if job.cancel_requested_at is None:
            job.cancel_requested_at = now
            await job_service.add_event(db, job.id, "cancel", "Stop requested")
        online = job.node_id is not None and manager.is_online(job.node_id)
        job.waiting_reason = STOPPING_REASON if online else f"Stops when {job.node_name or 'its node'} reconnects"
        targets.append(job)
    await db.commit()
    for job in targets:
        if job.waiting_reason != STOPPING_REASON:
            continue  # offline: told to stop on reconnect, or ended as cancelled by reconcile if it stays away
        try:
            sent = await manager.cancel(job)
        except Exception:  # a socket that died mid-send; reconcile handles the node going away
            log.warning("Couldn't send the stop request for job %s to node %s", job.id, job.node_name, exc_info=True)
            sent = False
        if not sent:
            reason = f"Stops when {job.node_name or 'its node'} reconnects"
            await db.execute(update(Job).where(Job.id == job.id, Job.state.in_(ACTIVE_JOB_STATES)).values(waiting_reason=reason).execution_options(synchronize_session=False))
            job.waiting_reason = reason
    await db.commit()
    for job in targets:
        job_service.publish_job(job)


def _running_summary(active: Sequence[Job]) -> dict[str, int]:
    out = {"stopping": 0, "stopping_offline": 0, "still_running": 0, "finalizing": 0}
    for job in active:
        if job.state == "finalizing":
            out["finalizing"] += 1
            out["still_running"] += 1
        elif job.cancel_requested_at is not None:
            out["stopping" if job.waiting_reason == STOPPING_REASON else "stopping_offline"] += 1
        else:
            out["still_running"] += 1
    return out


async def clear_queue(db: AsyncSession, *, stop_running: bool) -> dict[str, int]:
    """Cancel every queued job, and optionally ask nodes to stop running ones. Safe to repeat.

    Completed, failed and cancelled history is left alone. Cleared jobs move to Cancelled with a stop request,
    so scans don't queue their files again until the user retries them.
    """
    async with queue_lock:
        cancelled = await cancel_queued(db, job_ids=None, message=CLEARED_MESSAGE, by_user=True)
        await db.commit()
        active = list((await db.execute(select(Job).where(Job.state.in_(ACTIVE_JOB_STATES)).execution_options(populate_existing=True))).scalars())
        if stop_running:
            await request_stop(db, active)
    summary = {"cancelled": len(cancelled), **_running_summary(active)}
    log.info("Queue cleared (stop_running=%s): %s", stop_running, summary)
    bus.publish("queue.changed", summary)
    return summary
