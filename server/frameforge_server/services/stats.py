"""Dashboard statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ACTIVE_JOB_STATES, Job, Library, MediaFile, Node, StatsDaily
from .node_manager import manager


def _local_midnight_utc() -> datetime:
    local_now = datetime.now().astimezone()
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


async def overview(db: AsyncSession) -> dict[str, Any]:
    counts = dict((await db.execute(select(Job.state, func.count()).group_by(Job.state))).all())
    active = sum(counts.get(s, 0) for s in ACTIVE_JOB_STATES)
    since = _local_midnight_utc()
    completed_today = (await db.execute(select(func.count()).select_from(Job).where(Job.state == "completed", Job.finished_at >= since))).scalar_one()
    failed_today = (await db.execute(select(func.count()).select_from(Job).where(Job.state == "failed", Job.finished_at >= since))).scalar_one()

    totals = (await db.execute(select(func.coalesce(func.sum(StatsDaily.bytes_in), 0), func.coalesce(func.sum(StatsDaily.bytes_out), 0), func.coalesce(func.sum(StatsDaily.jobs_completed), 0)))).one()
    bytes_in, bytes_out, jobs_done = int(totals[0]), int(totals[1]), int(totals[2])

    lib_rows = (
        await db.execute(
            select(
                func.coalesce(func.sum(func.coalesce(MediaFile.original_size, MediaFile.size)), 0),
                func.coalesce(func.sum(MediaFile.size), 0),
                func.count(),
            ).where(MediaFile.status != "missing")
        )
    ).one()

    # Throughput & ETA from live progress of running jobs.
    active_jobs = (await db.execute(select(Job).where(Job.state.in_(ACTIVE_JOB_STATES)))).scalars().all()
    throughput = 0.0
    media_speed = 0.0
    remaining_media = 0.0
    for job in active_jobs:
        live = manager.progress.get(job.id)
        pct = (live.percent if live else job.progress) or 0.0
        speed = (live.speed if live else job.speed) or 0.0
        if job.source_duration:
            remaining_media += job.source_duration * max(0.0, 1 - pct / 100)
            if speed and job.source_size:
                throughput += speed * job.source_size / job.source_duration
        media_speed += speed
    queued_media = (await db.execute(select(func.coalesce(func.sum(Job.source_duration), 0)).where(Job.state == "queued"))).scalar_one()
    remaining_media += float(queued_media or 0)
    eta = remaining_media / media_speed if media_speed > 0 else None

    nodes_total = (await db.execute(select(func.count()).select_from(Node).where(Node.enabled.is_(True)))).scalar_one()
    return {
        "jobs": {
            "active": active,
            "queued": counts.get("queued", 0),
            "completed_today": completed_today,
            "failed_today": failed_today,
            "completed_total": jobs_done,
            "failed_total": counts.get("failed", 0),
        },
        "storage": {
            "processed_in": bytes_in,
            "processed_out": bytes_out,
            "saved": bytes_in - bytes_out,
            "library_original": int(lib_rows[0]),
            "library_current": int(lib_rows[1]),
            "library_files": int(lib_rows[2]),
        },
        "throughput_bps": throughput,
        "eta_seconds": eta,
        "nodes": {"online": len(manager.connections), "total": nodes_total},
    }


async def history(db: AsyncSession, days: int = 30) -> list[dict[str, Any]]:
    start = datetime.now().date() - timedelta(days=days - 1)
    rows = {r.day: r for r in (await db.execute(select(StatsDaily).where(StatsDaily.day >= start))).scalars()}
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        r = rows.get(d)
        out.append(
            {
                "day": d.isoformat(),
                "completed": r.jobs_completed if r else 0,
                "failed": r.jobs_failed if r else 0,
                "bytes_in": r.bytes_in if r else 0,
                "bytes_out": r.bytes_out if r else 0,
                "saved": (r.bytes_in - r.bytes_out) if r else 0,
            }
        )
    return out


async def library_stats(db: AsyncSession, library: Library) -> dict[str, Any]:
    rows = dict(
        (await db.execute(select(MediaFile.status, func.count()).where(MediaFile.library_id == library.id).group_by(MediaFile.status))).all()
    )
    sizes = (
        await db.execute(
            select(func.coalesce(func.sum(func.coalesce(MediaFile.original_size, MediaFile.size)), 0), func.coalesce(func.sum(MediaFile.size), 0)).where(
                MediaFile.library_id == library.id, MediaFile.status != "missing"
            )
        )
    ).one()
    return {"files_by_status": rows, "files": sum(rows.values()), "original_bytes": int(sizes[0]), "current_bytes": int(sizes[1])}
