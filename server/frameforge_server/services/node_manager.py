"""Server side of the node protocol: connections, live state, job reconciliation."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select

from frameforge_shared import PROTOCOL_VERSION, __version__
from frameforge_shared import protocol as P
from frameforge_shared.codecs import Backend
from frameforge_shared.protocol import NodeCapabilities, NodeMetrics

from ..auth.security import new_token, token_hash
from ..config import get_settings
from ..db.models import ACTIVE_JOB_STATES, Job, Node
from ..db.session import sessionmaker
from ..db.types import utcnow
from ..events import bus
from . import jobs as job_service
from .rules.conditions import HardwareSnapshot
from .system_settings import load_settings

log = logging.getLogger(__name__)

HELLO_TIMEOUT = 30
OFFLINE_GRACE = timedelta(seconds=120)
ASSIGN_ACK_TIMEOUT = timedelta(seconds=90)
PROGRESS_PERSIST_INTERVAL = 10.0
HISTORY_POINTS = 900  # 30 min at 2 s heartbeats
LAST_SEEN_PERSIST_INTERVAL = 30.0


@dataclass
class LiveProgress:
    percent: float = 0.0
    fps: float | None = None
    speed: float | None = None
    eta: float | None = None
    frame: int | None = None
    bitrate_kbps: float | None = None
    out_size: int | None = None
    elapsed: float = 0.0
    persisted_at: float = 0.0


@dataclass
class NodeConnection:
    node_id: int
    name: str
    ws: WebSocket
    caps: NodeCapabilities
    max_concurrency: int
    paused: bool
    enabled: bool
    reserve_slot_for_normal: bool
    constraints: dict[str, Any]
    path_mappings: list[dict[str, str]]
    metrics: NodeMetrics | None = None
    active_jobs: set[int] = field(default_factory=set)
    pending_assignments: dict[int, float] = field(default_factory=dict)  # job_id -> sent at (monotonic)
    history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=HISTORY_POINTS))
    connected_at: float = field(default_factory=time.time)
    last_seen_persist: float = 0.0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    features: set[str] = field(default_factory=set)  # Hello.features
    # A running compression preview uses the encoder like a job does (GPU session limits), so it takes a slot.
    preview_busy: bool = False

    @property
    def busy_slots(self) -> int:
        return len(self.active_jobs | set(self.pending_assignments)) + (1 if self.preview_busy else 0)

    @property
    def free_slots(self) -> int:
        return max(0, self.max_concurrency - self.busy_slots)

    async def send(self, msg_type: str, data: Any) -> None:
        async with self.send_lock:
            await self.ws.send_json(P.envelope(msg_type, data))


class NodeManager:
    def __init__(self) -> None:
        self.connections: dict[int, NodeConnection] = {}
        self.progress: dict[int, LiveProgress] = {}
        self.started_at = utcnow()
        self._offline_history: dict[int, deque[dict[str, Any]]] = {}

    # ------------------------------------------------------------------ queries

    def is_online(self, node_id: int) -> bool:
        return node_id in self.connections

    def history(self, node_id: int) -> list[dict[str, Any]]:
        conn = self.connections.get(node_id)
        if conn:
            return list(conn.history)
        return list(self._offline_history.get(node_id, []))

    def hardware_snapshot(self) -> HardwareSnapshot:
        snap = HardwareSnapshot(online_nodes=len(self.connections))
        for conn in self.connections.values():
            for enc in conn.caps.encoders:
                if not enc.verified:
                    continue
                snap.any_codecs.add(enc.codec)
                if enc.backend != Backend.CPU.value:
                    snap.hw_codecs.add(enc.codec)
        return snap

    def hardware_matrix(self) -> dict[str, dict[str, list[str]]]:
        """codec -> backend -> names of online nodes with a verified encoder for it."""
        out: dict[str, dict[str, list[str]]] = {}
        for conn in self.connections.values():
            for enc in conn.caps.encoders:
                if enc.verified:
                    out.setdefault(enc.codec, {}).setdefault(enc.backend, []).append(conn.name)
        return out

    def available_backends(self) -> dict[str, set[str]]:
        return {codec: set(by_backend) for codec, by_backend in self.hardware_matrix().items()}

    def live_view(self, node_id: int) -> dict[str, Any]:
        conn = self.connections.get(node_id)
        if not conn:
            return {"online": False}
        return {
            "online": True,
            "active_jobs": sorted(conn.active_jobs | set(conn.pending_assignments)),
            "metrics": conn.metrics.model_dump(mode="json") if conn.metrics else None,
            "connected_at": conn.connected_at,
        }

    # ------------------------------------------------------------------ local node

    async def ensure_local_node(self) -> str:
        """Create/refresh the built-in node used by role=all. Returns a fresh plaintext token."""
        settings = get_settings()
        token = new_token("ffn_")
        async with sessionmaker()() as db:
            node = (await db.execute(select(Node).where(Node.is_local.is_(True)))).scalar_one_or_none()
            if node is None:
                name = settings.node_name or f"{socket.gethostname()[:40]} (built-in)"
                existing = (await db.execute(select(Node).where(Node.name == name))).scalar_one_or_none()
                if existing is not None:
                    name = f"{name} {int(time.time()) % 1000}"
                node = Node(name=name, is_local=True, token_hash=token_hash(token), token_hint=token[:8], max_concurrency=1)
                db.add(node)
            else:
                node.token_hash = token_hash(token)
                node.token_hint = token[:8]
            await db.commit()
        return token

    # ------------------------------------------------------------------ socket lifecycle

    async def handle_socket(self, ws: WebSocket) -> None:
        auth = ws.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ws.query_params.get("token", "")
        async with sessionmaker()() as db:
            node = (await db.execute(select(Node).where(Node.token_hash == token_hash(token)))).scalar_one_or_none() if token else None
        if node is None:
            await ws.close(code=4401, reason="Invalid node token")
            return
        if not node.enabled:
            await ws.close(code=4403, reason="Node is disabled in FrameForge")
            return
        await ws.accept()
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=HELLO_TIMEOUT)
            if first.get("type") != P.NODE_HELLO:
                raise ValueError("expected hello")
            hello = P.Hello.model_validate(first.get("data") or {})
        except (asyncio.TimeoutError, ValueError, ValidationError, WebSocketDisconnect) as exc:
            log.warning("Node %s failed handshake: %s", node.name, exc)
            await _safe_close(ws, 4400, "Handshake failed")
            return
        if hello.protocol != PROTOCOL_VERSION:
            await ws.send_json(P.envelope(P.SERVER_ERROR, {"message": f"Protocol mismatch: server speaks v{PROTOCOL_VERSION}, node v{hello.protocol}. Update the node image."}))
            await _safe_close(ws, 4409, "Protocol mismatch")
            return

        old = self.connections.pop(node.id, None)
        if old is not None:
            _connection_closed(old)
            await _safe_close(old.ws, 4000, "Replaced by a new connection")

        conn = NodeConnection(
            node_id=node.id,
            name=node.name,
            ws=ws,
            caps=hello.capabilities,
            max_concurrency=node.max_concurrency,
            paused=node.paused,
            enabled=node.enabled,
            reserve_slot_for_normal=node.reserve_slot_for_normal,
            constraints=dict(node.constraints or {}),
            path_mappings=list(node.path_mappings or []),
            active_jobs=set(hello.active_jobs),
            features=set(hello.features),
        )
        if node.id in self._offline_history:
            conn.history.extend(self._offline_history.pop(node.id))
        self.connections[node.id] = conn

        async with sessionmaker()() as db:
            row = await db.get(Node, node.id)
            if row is not None:
                row.capabilities = hello.capabilities.model_dump(mode="json")
                row.version = hello.node_version
                row.last_seen_at = utcnow()
                await db.commit()
        await conn.send(
            P.SERVER_WELCOME,
            P.Welcome(node_id=node.id, name=node.name, max_concurrency=node.max_concurrency, path_mappings=[P.PathMapping(**m) for m in node.path_mappings or []]),
        )
        log.info("Node %s connected (v%s, %d active jobs)", node.name, hello.node_version, len(hello.active_jobs))
        bus.publish("node.status", {"id": node.id, "name": node.name, "online": True})
        await self._reconcile_on_connect(conn)

        from .scheduler import scheduler

        scheduler.wake()
        try:
            while True:
                msg = await ws.receive_json()
                await self._dispatch(conn, msg)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("Node %s connection error", conn.name)
        finally:
            _connection_closed(conn)
            if self.connections.get(node.id) is conn:
                self.connections.pop(node.id, None)
                self._offline_history[node.id] = conn.history
                async with sessionmaker()() as db:
                    row = await db.get(Node, node.id)
                    if row is not None:
                        row.last_seen_at = utcnow()
                        await db.commit()
                bus.publish("node.status", {"id": node.id, "name": node.name, "online": False})
                log.info("Node %s disconnected", conn.name)

    async def _reconcile_on_connect(self, conn: NodeConnection) -> None:
        async with sessionmaker()() as db:
            jobs = (await db.execute(select(Job).where(Job.node_id == conn.node_id, Job.state.in_(ACTIVE_JOB_STATES)))).scalars().all()
            for job in jobs:
                if job.id in conn.active_jobs:
                    continue
                if job.state == "finalizing" and job.finalize_plan:
                    await job_service.add_event(db, job.id, "recovery", "Node reconnected mid-finalize; asking it to verify and complete or roll back", level="warn")
                    conn.pending_assignments[job.id] = time.monotonic()
                    await conn.send(P.SERVER_RECOVER, P.RecoverJob(job_id=job.id, plan=P.FinalizePlan.model_validate(job.finalize_plan)))
                else:
                    await job_service.requeue(db, job, f"Node {conn.name} restarted while this job was running", count_attempt=False)
                    self.progress.pop(job.id, None)
                    job_service.publish_job(job)
            # Node is running jobs the server no longer considers active (e.g. cancelled while offline).
            known = {j.id: j for j in (await db.execute(select(Job).where(Job.id.in_(conn.active_jobs)))).scalars()} if conn.active_jobs else {}
            for job_id in list(conn.active_jobs):
                job = known.get(job_id)
                if job is None or job.state not in ACTIVE_JOB_STATES or job.node_id != conn.node_id:
                    await conn.send(P.SERVER_CANCEL, P.CancelJob(job_id=job_id))
            await db.commit()

    # ------------------------------------------------------------------ message handling

    async def _dispatch(self, conn: NodeConnection, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        data = msg.get("data") or {}
        try:
            if mtype == P.NODE_HEARTBEAT:
                await self._on_heartbeat(conn, P.Heartbeat.model_validate(data))
            elif mtype == P.NODE_JOB_PROGRESS:
                await self._on_progress(conn, P.JobProgress.model_validate(data))
            elif mtype == P.NODE_JOB_STAGE:
                await self._on_stage(conn, P.JobStage.model_validate(data))
            elif mtype == P.NODE_JOB_LOG:
                payload = P.JobLogLines.model_validate(data)
                await asyncio.to_thread(job_service.append_job_log, payload.job_id, payload.lines)
                bus.publish("job.log", {"id": payload.job_id, "lines": payload.lines[-50:]})
            elif mtype == P.NODE_JOB_RESULT:
                await self._on_result(conn, P.JobResult.model_validate(data))
            elif mtype == P.NODE_LOG:
                payload = P.NodeLog.model_validate(data)
                await asyncio.to_thread(_append_node_log, conn.node_id, payload.lines)
            elif mtype in (P.NODE_PREVIEW_PROGRESS, P.NODE_PREVIEW_CHUNK, P.NODE_PREVIEW_RESULT):
                from .previews import previews  # previews imports this module

                await previews.on_message(conn, mtype, data)
            elif mtype == P.NODE_CAPABILITIES:
                conn.caps = NodeCapabilities.model_validate(data)
                async with sessionmaker()() as db:
                    row = await db.get(Node, conn.node_id)
                    if row is not None:
                        row.capabilities = conn.caps.model_dump(mode="json")
                        await db.commit()
                bus.publish("node.updated", {"id": conn.node_id})
            else:
                log.debug("Unknown message from %s: %s", conn.name, mtype)
        except ValidationError as exc:
            log.warning("Invalid %s message from node %s: %s", mtype, conn.name, exc)

    async def _on_heartbeat(self, conn: NodeConnection, hb: P.Heartbeat) -> None:
        conn.metrics = hb.metrics
        conn.active_jobs = set(hb.active_jobs)
        for job_id in list(conn.pending_assignments):
            if job_id in conn.active_jobs:
                conn.pending_assignments.pop(job_id, None)
        m = hb.metrics
        gpu = m.gpus[0] if m.gpus else None
        point = {
            "t": m.timestamp,
            "cpu": round(m.cpu_percent, 1),
            "ram": round(100 * m.ram_used_mb / m.ram_total_mb, 1) if m.ram_total_mb else None,
            "gpu": gpu.utilization if gpu else None,
            "enc": gpu.encoder_utilization if gpu else None,
            "vram": round(100 * gpu.vram_used_mb / gpu.vram_total_mb, 1) if gpu and gpu.vram_used_mb is not None and gpu.vram_total_mb else None,
        }
        conn.history.append(point)
        bus.publish("node.metrics", {"id": conn.node_id, "metrics": m.model_dump(mode="json"), "point": point, "active_jobs": sorted(conn.active_jobs)})
        now = time.monotonic()
        if now - conn.last_seen_persist > LAST_SEEN_PERSIST_INTERVAL:
            conn.last_seen_persist = now
            async with sessionmaker()() as db:
                row = await db.get(Node, conn.node_id)
                if row is not None:
                    row.last_seen_at = utcnow()
                    row.last_metrics = m.model_dump(mode="json")
                    await db.commit()

    async def _on_progress(self, conn: NodeConnection, p: P.JobProgress) -> None:
        live = self.progress.setdefault(p.job_id, LiveProgress())
        live.percent, live.fps, live.speed, live.eta = p.percent, p.fps, p.speed, p.eta
        live.frame, live.bitrate_kbps, live.out_size, live.elapsed = p.frame, p.bitrate_kbps, p.out_size, p.elapsed
        gpu_util = conn.metrics.gpus[0].utilization if conn.metrics and conn.metrics.gpus else None
        bus.publish("job.progress", {**p.model_dump(mode="json"), "id": p.job_id, "node_id": conn.node_id, "gpu_util": gpu_util})
        now = time.monotonic()
        if now - live.persisted_at >= PROGRESS_PERSIST_INTERVAL:
            live.persisted_at = now
            async with sessionmaker()() as db:
                job = await db.get(Job, p.job_id)
                if job is not None and job.state in ACTIVE_JOB_STATES:
                    job.progress, job.fps, job.speed, job.eta_seconds = p.percent, p.fps, p.speed, p.eta
                    await db.commit()

    async def _on_stage(self, conn: NodeConnection, st: P.JobStage) -> None:
        conn.pending_assignments.pop(st.job_id, None)
        conn.active_jobs.add(st.job_id)
        async with sessionmaker()() as db:
            job = await db.get(Job, st.job_id)
            if job is None or job.state not in ACTIVE_JOB_STATES:
                return
            job.state = st.stage
            if st.stage == "transcoding" and job.started_at is None:
                job.started_at = utcnow()
            if st.data.get("encoder"):
                job.encoder = st.data["encoder"]
            if "hw_decode" in st.data:
                job.hw_decode = bool(st.data["hw_decode"])
            if st.message:
                level = str(st.data.get("level", "info"))
                await job_service.add_event(db, job.id, st.stage, st.message, level=level, data={k: v for k, v in st.data.items() if k != "level"})
            await db.commit()
            job_service.publish_job(job)

    async def _on_result(self, conn: NodeConnection, result: P.JobResult) -> None:
        conn.pending_assignments.pop(result.job_id, None)
        conn.active_jobs.discard(result.job_id)
        self.progress.pop(result.job_id, None)
        settings = await load_settings()
        async with sessionmaker()() as db:
            job = await db.get(Job, result.job_id)
            if job is None:
                return
            if job.state not in ACTIVE_JOB_STATES and not (job.state == "cancelled" and result.status == "cancelled"):
                log.warning("Ignoring result for job %s in state %s", job.id, job.state)
                return
            await job_service.record_result(db, job, result, settings.max_attempts)
            await db.commit()
            job_service.publish_job(job)
        bus.publish("stats.changed", {})
        from .scheduler import scheduler

        scheduler.wake()

    # ------------------------------------------------------------------ commands

    async def send_assignment(self, node_id: int, assignment: P.JobAssignment) -> bool:
        conn = self.connections.get(node_id)
        if conn is None:
            return False
        conn.pending_assignments[assignment.job_id] = time.monotonic()
        try:
            await conn.send(P.SERVER_ASSIGN, assignment)
            return True
        except Exception:
            conn.pending_assignments.pop(assignment.job_id, None)
            log.exception("Failed to send job %s to node %s", assignment.job_id, conn.name)
            return False

    async def cancel(self, job: Job) -> bool:
        """Ask the node to stop. Returns False if the node isn't connected."""
        if job.node_id is None:
            return False
        conn = self.connections.get(job.node_id)
        if conn is None:
            return False
        await conn.send(P.SERVER_CANCEL, P.CancelJob(job_id=job.id))
        return True

    async def push_config(self, node: Node) -> None:
        conn = self.connections.get(node.id)
        if conn is None:
            return
        conn.max_concurrency = node.max_concurrency
        conn.paused = node.paused
        conn.enabled = node.enabled
        conn.reserve_slot_for_normal = node.reserve_slot_for_normal
        conn.constraints = dict(node.constraints or {})
        conn.path_mappings = list(node.path_mappings or [])
        conn.name = node.name
        await conn.send(P.SERVER_CONFIG, P.NodeConfig(max_concurrency=node.max_concurrency, path_mappings=[P.PathMapping(**m) for m in node.path_mappings or []]))
        if not node.enabled:
            await _safe_close(conn.ws, 4403, "Node disabled")

    async def request_redetect(self, node_id: int) -> bool:
        conn = self.connections.get(node_id)
        if conn is None:
            return False
        await conn.send(P.SERVER_REDETECT, {})
        return True

    async def disconnect(self, node_id: int, reason: str) -> None:
        conn = self.connections.get(node_id)
        if conn:
            await _safe_close(conn.ws, 4001, reason)

    # ------------------------------------------------------------------ reconciliation loop

    async def reconcile(self) -> None:
        """Requeue jobs whose node vanished, and assignments that were never acknowledged."""
        now = utcnow()
        async with sessionmaker()() as db:
            jobs = (await db.execute(select(Job).where(Job.state.in_(ACTIVE_JOB_STATES)))).scalars().all()
            changed = []
            for job in jobs:
                conn = self.connections.get(job.node_id) if job.node_id else None
                if conn is not None:
                    sent = conn.pending_assignments.get(job.id)
                    if job.state == "assigned" and job.id not in conn.active_jobs and job.assigned_at and now - job.assigned_at > ASSIGN_ACK_TIMEOUT and sent is not None:
                        conn.pending_assignments.pop(job.id, None)
                        await job_service.requeue(db, job, f"Node {conn.name} didn't start the job in time", count_attempt=False)
                        changed.append(job)
                    continue
                node = await db.get(Node, job.node_id) if job.node_id else None
                offline_since = max(filter(None, [node.last_seen_at if node else None, self.started_at, job.assigned_at]))
                if now - offline_since < OFFLINE_GRACE:
                    continue
                name = node.name if node else "unknown"
                if job.state == "finalizing":
                    reason = f"Waiting for node {name} to reconnect so it can safely finish (or roll back) replacing the file"
                    if job.waiting_reason != reason:
                        job.waiting_reason = reason
                        await job_service.add_event(db, job.id, "waiting", reason, level="warn")
                        changed.append(job)
                    continue
                self.progress.pop(job.id, None)
                await job_service.requeue(db, job, f"Node {name} went offline during the job", count_attempt=True)
                changed.append(job)
            if changed:
                await db.commit()
                for job in changed:
                    job_service.publish_job(job)


def _append_node_log(node_id: int, lines: list[str]) -> None:
    if not lines:
        return
    path = get_settings().node_logs_dir / f"node-{node_id}.log"
    if path.exists() and path.stat().st_size > 20 * 1024 * 1024:
        path.replace(path.with_suffix(".log.1"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


async def _safe_close(ws: WebSocket, code: int, reason: str) -> None:
    try:
        await ws.close(code=code, reason=reason)
    except Exception:
        pass


manager = NodeManager()

__all__ = ["manager", "NodeManager", "NodeConnection", "__version__"]


def _connection_closed(conn: NodeConnection) -> None:
    """Fail whatever was waiting on this socket (previews). Jobs are reconciled separately."""
    from .previews import previews  # previews imports this module

    previews.on_connection_closed(conn)
