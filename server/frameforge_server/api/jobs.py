"""Queue & history endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.sessions import require_user
from ..db.models import ACTIVE_JOB_STATES, TERMINAL_JOB_STATES, Job, JobEvent, MediaFile, Profile
from ..db.session import get_db
from ..db.types import utcnow
from ..logging_setup import tail_file
from ..services import jobs as job_service
from ..services.node_manager import manager
from ..services.scheduler import scheduler

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_user)])

STATE_GROUPS = {
    "active": ACTIVE_JOB_STATES,
    "queued": ("queued",),
    "completed": ("completed",),
    "failed": ("failed",),
    "cancelled": ("cancelled",),
    "finished": TERMINAL_JOB_STATES,
}


def _with_live(job: Job) -> dict:
    view = job_service.job_summary(job)
    live = manager.progress.get(job.id)
    if live and job.state in ACTIVE_JOB_STATES:
        view.update(progress=live.percent, fps=live.fps, speed=live.speed, eta_seconds=live.eta, frame=live.frame, bitrate_kbps=live.bitrate_kbps, out_size=live.out_size, elapsed=live.elapsed)
    return view


@router.get("")
async def list_jobs(
    group: str | None = None,
    library_id: int | None = None,
    q: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(Job)
    if group:
        states = STATE_GROUPS.get(group)
        if states is None:
            raise HTTPException(400, f"Unknown group '{group}'")
        stmt = stmt.where(Job.state.in_(states))
    if library_id is not None:
        stmt = stmt.where(Job.library_id == library_id)
    if q:
        stmt = stmt.where(Job.source_path.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    if group == "queued":
        stmt = stmt.order_by(Job.priority.desc(), Job.id.asc())
    elif group in ("completed", "failed", "cancelled", "finished"):
        stmt = stmt.order_by(Job.finished_at.desc().nulls_last(), Job.id.desc())
    else:
        stmt = stmt.order_by(Job.id.desc())
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    counts = dict((await db.execute(select(Job.state, func.count()).group_by(Job.state))).all())
    group_counts = {g: sum(counts.get(s, 0) for s in states) for g, states in STATE_GROUPS.items()}
    return {"total": total, "items": [_with_live(j) for j in rows], "counts": group_counts}


async def _get(db: AsyncSession, job_id: int) -> Job:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    job = await _get(db, job_id)
    events = (await db.execute(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id))).scalars().all()
    return {
        **_with_live(job),
        "profile_spec": job.profile_spec,
        "diagnosis": job.diagnosis or None,
        "validation": job.validation or None,
        "notes": job.notes,
        "finalize_plan": job.finalize_plan or None,
        "excluded_nodes": job.excluded_nodes,
        "events": [{"id": e.id, "at": e.at.isoformat(), "level": e.level, "kind": e.kind, "message": e.message, "data": e.data} for e in events],
    }


@router.get("/{job_id}/log", response_class=PlainTextResponse)
async def job_log(job_id: int, lines: int = Query(2000, ge=10, le=100000)) -> str:
    return "\n".join(await asyncio.to_thread(tail_file, job_service.job_log_path(job_id), lines))


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    job = await _get(db, job_id)
    if job.state in TERMINAL_JOB_STATES:
        raise HTTPException(409, "This job has already finished")
    if job.state == "finalizing":
        raise HTTPException(409, "The output is being moved into place right now. Cancelling could leave files half-replaced, so it isn't allowed at this step.")
    if job.state in ACTIVE_JOB_STATES and await manager.cancel(job):
        job.waiting_reason = "Cancelling…"
        await job_service.add_event(db, job.id, "cancel", "Cancel requested")
        await db.commit()
        job_service.publish_job(job)
        return job_service.job_summary(job)
    job.state = "cancelled"
    job.finished_at = utcnow()
    job.waiting_reason = None
    await job_service.add_event(db, job.id, "cancelled", "Cancelled before it started. Nothing was changed.")
    if job.file_id:
        f = await db.get(MediaFile, job.file_id)
        if f is not None and f.status in ("queued", "processing"):
            f.status = "ready"
    await db.commit()
    job_service.publish_job(job)
    scheduler.wake()
    return job_service.job_summary(job)


@router.post("/{job_id}/retry", status_code=201)
async def retry_job(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    job = await _get(db, job_id)
    if job.state not in ("failed", "cancelled"):
        raise HTTPException(409, "Only failed or cancelled jobs can be retried")
    f = await db.get(MediaFile, job.file_id) if job.file_id else None
    if f is None or f.status == "missing":
        raise HTTPException(400, "The source file is no longer in the library")
    profile = await db.get(Profile, job.profile_id) if job.profile_id else None
    if profile is None:
        raise HTTPException(400, "The profile used by this job was deleted")
    if await job_service.active_job_for_file(db, f.id):
        raise HTTPException(409, "This file already has a job in the queue")
    new = await job_service.create_job(db, f, profile, priority=job.priority, manual=True)
    await job_service.add_event(db, new.id, "retry", f"Retry of job #{job.id}")
    await db.commit()
    job_service.publish_job(new, "job.created")
    scheduler.wake()
    return job_service.job_summary(new)


class PriorityRequest(BaseModel):
    priority: int = Field(ge=0, le=4)


@router.post("/{job_id}/priority")
async def set_priority(job_id: int, body: PriorityRequest, db: AsyncSession = Depends(get_db)) -> dict:
    job = await _get(db, job_id)
    if job.state != "queued":
        raise HTTPException(409, "Priority can only be changed while the job is queued")
    job.priority = body.priority
    await db.commit()
    job_service.publish_job(job)
    scheduler.wake()
    return job_service.job_summary(job)


@router.delete("/{job_id}")
async def delete_job(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    job = await _get(db, job_id)
    if job.state not in TERMINAL_JOB_STATES:
        raise HTTPException(409, "Cancel the job before removing it")
    await db.delete(job)
    await db.commit()
    job_service.cleanup_job_log(job_id)
    return {"ok": True}


class ClearRequest(BaseModel):
    states: list[str] = Field(default_factory=lambda: ["completed", "cancelled"])


@router.post("/clear")
async def clear_jobs(body: ClearRequest, db: AsyncSession = Depends(get_db)) -> dict:
    states = [s for s in body.states if s in TERMINAL_JOB_STATES]
    ids = list((await db.execute(select(Job.id).where(Job.state.in_(states)))).scalars())
    await db.execute(delete(Job).where(Job.id.in_(ids)))
    await db.commit()
    for job_id in ids:
        job_service.cleanup_job_log(job_id)
    return {"removed": len(ids)}
