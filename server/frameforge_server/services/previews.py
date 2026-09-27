"""Compression previews: a node encodes a few short samples of a real file exactly like a job would,
then sends back matching frames (original vs encoded) as PNG images.

Previews are a disposable cache: state lives in memory, images in ``config_dir/previews``, which is
wiped at startup and expired after ``PREVIEW_TTL``. A running preview takes a slot on its node so it
never pushes a GPU past its session limit while jobs run.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from frameforge_shared import protocol as P
from frameforge_shared.compat import required_node_features
from frameforge_shared.encoders import select_encoder
from frameforge_shared.media import MediaInfo
from frameforge_shared.profile import ProfileSpec

from ..config import get_settings
from ..events import bus
from .node_manager import NodeConnection, manager

log = logging.getLogger(__name__)

PREVIEW_TTL = 6 * 3600
INACTIVITY_TIMEOUT = 60.0
HARD_TIMEOUT = 600.0
WATCH_INTERVAL = 2.0
MAX_PREVIEWS = 30
MAX_PREVIEW_BYTES = 400 * 1024 * 1024
DEFAULT_POSITIONS = (0.2, 0.5, 0.8)
MAX_POSITIONS = 5
SAMPLE_SECONDS = 2.0
PREROLL_SECONDS = 2.0
_IMAGE_NAME = re.compile(r"^\d{1,2}-(original|encoded)\.png$")


class PreviewUnavailable(Exception):
    """No node can make this preview right now (the message says why)."""


@dataclass(eq=False)
class Preview:
    id: str
    file_id: int
    filename: str
    node_id: int
    node_name: str
    spec: dict[str, Any]
    source_size: int
    source_duration: float
    directory: Path
    conn: NodeConnection | None
    state: str = "running"  # running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.monotonic)
    started: float = field(default_factory=time.monotonic)
    done: int = 0
    total: int = 0
    message: str | None = None
    samples: list[P.PreviewSample] = field(default_factory=list)
    encoder: str | None = None
    quality_label: str | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    received_bytes: int = 0


def _estimate(p: Preview) -> dict[str, Any] | None:
    """Video size estimate from the samples. Honest about being rough: it's a range from a few seconds of footage."""
    rates = [s.encoded_bytes / s.sample_seconds for s in p.samples if s.sample_seconds > 0]
    if not rates or p.source_duration <= 0:
        return None
    low, high, mean = min(rates), max(rates), sum(rates) / len(rates)
    est = mean * p.source_duration
    return {
        "video_bytes": int(est),
        "video_bytes_low": int(low * p.source_duration),
        "video_bytes_high": int(high * p.source_duration),
        "ratio": est / p.source_size if p.source_size else None,
        "basis": f"{len(rates)} sample{'s' if len(rates) != 1 else ''} of a few seconds; video only, audio not included",
    }


class PreviewService:
    def __init__(self) -> None:
        self.items: dict[str, Preview] = {}
        self._watch: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    def reset_cache(self) -> None:
        root = get_settings().previews_dir
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

    def view(self, p: Preview) -> dict[str, Any]:
        base = f"/api/v1/previews/{p.id}/frames"
        return {
            "id": p.id,
            "file_id": p.file_id,
            "filename": p.filename,
            "node_id": p.node_id,
            "node_name": p.node_name,
            "state": p.state,
            "done": p.done,
            "total": p.total,
            "message": p.message,
            "encoder": p.encoder,
            "quality_label": p.quality_label,
            "notes": p.notes,
            "error": p.error,
            "created_at": p.created,
            "samples": [
                {
                    "index": s.index,
                    "position": s.position,
                    "width": s.width,
                    "height": s.height,
                    "original_url": f"{base}/{s.index}/original.png",
                    "encoded_url": f"{base}/{s.index}/encoded.png",
                }
                for s in p.samples
            ],
            "estimate": _estimate(p) if p.state == "done" else None,
        }

    def _publish(self, p: Preview) -> None:
        bus.publish("preview.updated", self.view(p))

    # ------------------------------------------------------------------ choosing a node

    def candidates(self, spec: ProfileSpec, source: MediaInfo | None) -> tuple[list[tuple[NodeConnection, P.EncoderChoice]], list[str]]:
        found: list[tuple[NodeConnection, P.EncoderChoice]] = []
        reasons: list[str] = []
        needed = required_node_features(spec) | {P.FEATURE_PREVIEW}
        for conn in manager.connections.values():
            if not conn.enabled or conn.paused:
                reasons.append(f"{conn.name}: {'disabled' if not conn.enabled else 'paused'}")
                continue
            if needed - conn.features:
                reasons.append(f"{conn.name}: needs a newer FrameForge node for previews")
                continue
            if conn.preview_busy:
                reasons.append(f"{conn.name}: already making a preview")
                continue
            if conn.free_slots <= 0:
                reasons.append(f"{conn.name}: all slots are busy with jobs")
                continue
            sel = select_encoder(spec, conn.caps, source)
            if sel.choice is None:
                reasons.append(f"{conn.name}: {sel.reason}")
                continue
            found.append((conn, sel.choice))
        # Same preference as the scheduler: hardware first, then the least busy node.
        found.sort(key=lambda c: (c[1].backend == "cpu", -c[0].free_slots))
        return found, reasons

    def availability(self, spec: ProfileSpec) -> dict[str, Any]:
        if spec.is_remux:
            return {"available": False, "reason": "Copying the video doesn't change it, so there's nothing to preview."}
        if not manager.connections:
            return {"available": False, "reason": "No nodes are online."}
        found, reasons = self.candidates(spec, None)
        if found:
            return {"available": True, "reason": None, "node": found[0][0].name}
        return {"available": False, "reason": "; ".join(reasons[:3]) or "No node can encode this profile."}

    # ------------------------------------------------------------------ requests

    async def start(self, *, file_id: int, source_path: str, filename: str, source: MediaInfo, spec: ProfileSpec, node_id: int | None, positions: list[float] | None) -> Preview:
        if spec.is_remux:
            raise PreviewUnavailable("Copying the video doesn't change it, so there's nothing to preview.")
        if source.video is None:
            raise PreviewUnavailable("This file has no video stream.")
        found, reasons = self.candidates(spec, source)
        if node_id is not None:
            found = [c for c in found if c[0].node_id == node_id]
        if not found:
            raise PreviewUnavailable("; ".join(reasons[:3]) or "No node can make this preview right now.")
        conn, choice = found[0]

        duration = max(0.0, source.duration)
        fractions = positions or list(DEFAULT_POSITIONS)
        seconds = sorted({round(min(max(0.0, f), 1.0) * duration, 3) for f in fractions[:MAX_POSITIONS]}) if duration else [0.0]
        seconds = [min(s, max(0.0, duration - SAMPLE_SECONDS)) for s in seconds]

        self._evict()
        pid = uuid.uuid4().hex
        directory = get_settings().previews_dir / pid
        directory.mkdir(parents=True, exist_ok=True)
        p = Preview(
            id=pid,
            file_id=file_id,
            filename=filename,
            node_id=conn.node_id,
            node_name=conn.name,
            spec=spec.model_dump(mode="json"),
            source_size=source.size,
            source_duration=duration,
            directory=directory,
            conn=conn,
            total=len(seconds),
        )
        self.items[pid] = p
        conn.preview_busy = True
        request = P.PreviewRequest(
            request_id=pid,
            source_path=source_path,
            profile=spec,
            encoder=choice,
            source_media=source,
            positions=seconds,
            sample_seconds=SAMPLE_SECONDS,
            preroll_seconds=PREROLL_SECONDS,
        )
        try:
            await conn.send(P.SERVER_PREVIEW, request)
        except Exception as exc:
            self._finish(p, "failed", f"Couldn't reach {conn.name}: {exc}")
            raise PreviewUnavailable(f"Couldn't reach {conn.name}") from exc
        self._ensure_watch()
        self._publish(p)
        return p

    async def cancel(self, pid: str) -> Preview | None:
        p = self.items.get(pid)
        if p is None:
            return None
        if p.state == "running" and p.conn is not None:
            with contextlib.suppress(Exception):
                await p.conn.send(P.SERVER_PREVIEW_CANCEL, P.PreviewCancel(request_id=pid))
            self._finish(p, "cancelled", None)
        return p

    def frame_path(self, pid: str, index: int, kind: str) -> Path | None:
        p = self.items.get(pid)
        if p is None or kind not in ("original", "encoded"):
            return None
        path = p.directory / f"{index}-{kind}.png"
        return path if path.is_file() else None

    # ------------------------------------------------------------------ node messages

    async def on_message(self, conn: NodeConnection, mtype: str, data: dict[str, Any]) -> None:
        try:
            if mtype == P.NODE_PREVIEW_PROGRESS:
                msg = P.PreviewProgress.model_validate(data)
                p = self._own(conn, msg.request_id)
                if p:
                    p.done, p.total, p.message = msg.done, msg.total, msg.message
                    p.last_activity = time.monotonic()
                    self._publish(p)
            elif mtype == P.NODE_PREVIEW_CHUNK:
                chunk = P.PreviewChunk.model_validate(data)
                p = self._own(conn, chunk.request_id)
                if p:
                    await self._write_chunk(p, chunk)
            elif mtype == P.NODE_PREVIEW_RESULT:
                res = P.PreviewResult.model_validate(data)
                p = self._own(conn, res.request_id)
                if p:
                    self._on_result(p, res)
        except ValidationError as exc:
            log.warning("Invalid %s from %s: %s", mtype, conn.name, exc)

    def _own(self, conn: NodeConnection, pid: str) -> Preview | None:
        p = self.items.get(pid)
        if p is None or p.conn is not conn or p.state != "running":
            return None
        return p

    async def _write_chunk(self, p: Preview, chunk: P.PreviewChunk) -> None:
        if not _IMAGE_NAME.match(chunk.name):
            self._finish(p, "failed", "The node sent an unexpected file")
            return
        try:
            data = base64.b64decode(chunk.data, validate=True)
        except ValueError:
            self._finish(p, "failed", "The node sent a damaged image")
            return
        p.received_bytes += len(data)
        if p.received_bytes > MAX_PREVIEW_BYTES:
            self._finish(p, "failed", "The preview images are too large")
            return
        path = p.directory / chunk.name
        current = path.stat().st_size if path.exists() else 0
        if chunk.offset != current:
            self._finish(p, "failed", "Preview images arrived out of order")
            return
        await asyncio.to_thread(_append, path, data)
        p.last_activity = time.monotonic()

    def _on_result(self, p: Preview, res: P.PreviewResult) -> None:
        if not res.ok:
            self._finish(p, "failed", res.error or "The node couldn't make the preview")
            return
        missing = [n for s in res.samples for n in (s.original, s.encoded) if not (p.directory / n).is_file() or not _IMAGE_NAME.match(n)]
        if missing:
            self._finish(p, "failed", "Some preview images didn't arrive")
            return
        p.samples = res.samples
        p.encoder, p.quality_label, p.notes = res.encoder, res.quality_label, res.notes
        p.done = p.total
        self._finish(p, "done", None)

    def _finish(self, p: Preview, state: str, error: str | None) -> None:
        if p.state != "running":
            return
        p.state, p.error = state, error
        if p.conn is not None:
            p.conn.preview_busy = False
            p.conn = None
        if state != "done":
            shutil.rmtree(p.directory, ignore_errors=True)
        self._publish(p)
        if state == "done":
            from .scheduler import scheduler  # a slot is free again

            scheduler.wake()

    def on_connection_closed(self, conn: NodeConnection) -> None:
        for p in list(self.items.values()):
            if p.conn is conn and p.state == "running":
                self._finish(p, "failed", f"{conn.name} disconnected")

    # ------------------------------------------------------------------ housekeeping

    def expire(self) -> None:
        self._evict()

    def _evict(self) -> None:
        now = time.time()
        for p in list(self.items.values()):
            if p.state != "running" and now - p.created > PREVIEW_TTL:
                self._drop(p)
        finished = sorted((p for p in self.items.values() if p.state != "running"), key=lambda p: p.created)
        while len(self.items) >= MAX_PREVIEWS and finished:
            self._drop(finished.pop(0))

    def _drop(self, p: Preview) -> None:
        self.items.pop(p.id, None)
        shutil.rmtree(p.directory, ignore_errors=True)

    def _ensure_watch(self) -> None:
        if self._watch is None or self._watch.done():
            self._watch = asyncio.create_task(self._watchdog(), name="preview-watchdog")

    async def _watchdog(self) -> None:
        while any(p.state == "running" for p in self.items.values()):
            await asyncio.sleep(WATCH_INTERVAL)
            now = time.monotonic()
            for p in list(self.items.values()):
                if p.state != "running":
                    continue
                if now - p.last_activity > INACTIVITY_TIMEOUT:
                    reason = f"{p.node_name} stopped responding"
                elif now - p.started > HARD_TIMEOUT:
                    reason = "The preview took too long"
                else:
                    continue
                if p.conn is not None:
                    with contextlib.suppress(Exception):
                        await p.conn.send(P.SERVER_PREVIEW_CANCEL, P.PreviewCancel(request_id=p.id))
                self._finish(p, "failed", reason)


def _append(path: Path, data: bytes) -> None:
    with open(path, "ab") as fh:
        fh.write(data)


previews = PreviewService()
