"""Priority- and resource-aware job dispatcher.

Nodes don't poll: the scheduler pushes assignments to connected nodes that have free slots.
Strict priority tiers (Critical → Background), FIFO within a tier.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from frameforge_shared import protocol as P
from frameforge_shared.compat import required_node_features
from frameforge_shared.encoders import select_encoder
from frameforge_shared.media import MediaInfo
from frameforge_shared.profile import ProfileSpec

from ..db.models import Job, Library, MediaFile
from ..db.session import sessionmaker
from ..db.types import utcnow
from . import jobs as job_service
from .node_manager import NodeConnection, manager
from .system_settings import GeneralSettings, load_settings
from .timewindow import describe_window, in_window

log = logging.getLogger(__name__)

TICK_SECONDS = 5
MAX_CANDIDATES = 500
PRIORITY_NORMAL = 2


@dataclass
class Placement:
    conn: NodeConnection
    choice: P.EncoderChoice
    score: tuple[Any, ...]


def _job_window_block(job: Job, settings: GeneralSettings) -> str | None:
    window = (job.schedule or {}).get("window")
    if window and not in_window(window):
        return f"Scheduled to run {describe_window(window)}"
    qh = settings.quiet_hours
    if qh.enabled and not job.manual:
        limit = 0 if qh.applies_to == "background" else 1
        if job.priority <= limit and not in_window(qh.window.model_dump()):
            return f"Background work runs {describe_window(qh.window.model_dump())}"
    return None


def _node_block(conn: NodeConnection, job: Job) -> str | None:
    if not conn.enabled:
        return "disabled"
    if conn.paused:
        return "paused"
    if conn.node_id in (job.excluded_nodes or []):
        return "declined this job earlier"
    if conn.free_slots <= 0:
        return "all slots busy"
    if conn.reserve_slot_for_normal and conn.free_slots == 1 and conn.max_concurrency > 1 and job.priority < PRIORITY_NORMAL:
        return "last slot reserved for Normal+ priority"
    c = conn.constraints or {}
    window = c.get("window")
    if window and not in_window(window):
        return f"only works {describe_window(window)}"
    # Utilization limits guard *other* workloads on the machine (gaming, editing), so they're checked
    # only while FrameForge itself isn't using the node; otherwise our own jobs would block us.
    if conn.metrics and conn.busy_slots == 0:
        max_gpu = c.get("max_gpu_util")
        if max_gpu is not None and conn.metrics.gpus:
            util = max((g.utilization or 0) for g in conn.metrics.gpus)
            if util > max_gpu:
                return f"GPU busy ({util:.0f}% > {max_gpu}%)"
        max_cpu = c.get("max_cpu_util")
        if max_cpu is not None and conn.metrics.cpu_percent > max_cpu:
            return f"CPU busy ({conn.metrics.cpu_percent:.0f}% > {max_cpu}%)"
    return None


class Scheduler:
    def __init__(self) -> None:
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        tick = 0
        while not stop.is_set():
            try:
                await self.tick()
                tick += 1
                if tick % 3 == 0:
                    await manager.reconcile()
            except Exception:
                log.exception("Scheduler tick failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self) -> None:
        settings = await load_settings()
        async with sessionmaker()() as db:
            q = select(Job).where(Job.state == "queued").order_by(Job.priority.desc(), Job.id.asc()).limit(MAX_CANDIDATES)
            queued = list((await db.execute(q)).scalars())
            if not queued:
                return
            conns = list(manager.connections.values())
            libraries: dict[int, Library] = {}
            dirty: list[Job] = []
            for job in queued:
                reason = await self._try_place(db, job, conns, settings, libraries)
                if reason is None:
                    dirty.append(job)
                elif job.waiting_reason != reason:
                    job.waiting_reason = reason
                    dirty.append(job)
                if not any(c.free_slots > 0 for c in conns):
                    # No capacity anywhere: remaining jobs just wait for a slot.
                    for other in queued:
                        if other.state == "queued" and other.waiting_reason is None:
                            other.waiting_reason = "Waiting for a free node slot"
                            dirty.append(other)
                    break
            await db.commit()
            for job in dirty:
                job_service.publish_job(job)

    async def _try_place(self, db, job: Job, conns: list[NodeConnection], settings: GeneralSettings, libraries: dict[int, Library]) -> str | None:  # noqa: ANN001
        """Assign the job if possible. Returns None on success, otherwise a human-readable waiting reason."""
        block = _job_window_block(job, settings)
        if block:
            return block
        if not conns:
            return "No nodes are online"
        try:
            spec = ProfileSpec.model_validate(job.profile_spec)
        except ValueError:
            return "Profile snapshot is invalid"
        file = await db.get(MediaFile, job.file_id) if job.file_id else None
        source = MediaInfo.model_validate(file.meta.info) if file and file.meta and file.meta.info else None

        candidates: list[Placement] = []
        reasons: list[str] = []
        for conn in conns:
            why = _node_block(conn, job)
            if why:
                reasons.append(f"{conn.name}: {why}")
                continue
            missing = required_node_features(spec) - conn.features
            if missing:
                reasons.append(f"{conn.name}: needs a newer FrameForge node for this profile's audio settings")
                continue
            sel = select_encoder(spec, conn.caps, source)
            if sel.choice is None:
                reasons.append(f"{conn.name}: {sel.reason}")
                continue
            is_hw = sel.choice.backend != "cpu"
            util = conn.metrics.cpu_percent if conn.metrics else 50.0
            candidates.append(Placement(conn, sel.choice, (not is_hw, -conn.free_slots, util)))
        if not candidates:
            return "; ".join(reasons[:3]) if reasons else "No suitable node"

        best = min(candidates, key=lambda p: p.score)
        if job.library_id not in libraries:
            lib = await db.get(Library, job.library_id) if job.library_id else None
            if lib is None:
                return "The job's library no longer exists"
            libraries[job.library_id] = lib
        library = libraries[job.library_id]  # type: ignore[index]
        plan = job_service.build_plan(job, library, spec)
        assignment = P.JobAssignment(
            job_id=job.id,
            source_path=job.source_path,
            profile=spec,
            encoder=best.choice,
            plan=plan,
            thresholds=job_service.thresholds_for(library),
            source_media=source,
            frameforge_tag=job_service.frameforge_tag(job),
        )
        job.state = "assigned"
        job.node_id = best.conn.node_id
        job.node_name = best.conn.name
        job.encoder = best.choice.encoder
        job.backend = best.choice.backend
        job.hw_decode = best.choice.hw_decode
        job.finalize_plan = plan.model_dump(mode="json")
        job.assigned_at = utcnow()
        job.waiting_reason = None
        job.progress = 0.0
        if file is not None:
            file.status = "processing"
        await job_service.add_event(
            db, job.id, "assigned", f"Sent to {best.conn.name} using {best.choice.encoder}" + (" with hardware decoding" if best.choice.hw_decode else "")
        )
        await db.commit()  # commit before sending: the node may report back immediately
        if not await manager.send_assignment(best.conn.node_id, assignment):
            await job_service.requeue(db, job, f"Couldn't reach {best.conn.name}", count_attempt=False)
            if file is not None:
                file.status = "queued"
            return job.waiting_reason
        return None


scheduler = Scheduler()
