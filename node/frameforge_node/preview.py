"""Compression previews on the node: encode short samples like a real job and send matching frames back.

Previews never touch library folders. Samples and images live in the node's own state directory and
are removed afterwards; the PNGs travel to the server in chunks over the socket (they are small
images, not media files). One preview at a time; a second request is refused, not queued.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from frameforge_shared import protocol as P
from frameforge_shared.ffmpeg_builder import BuildError, PreviewBuild, build_preview_commands
from frameforge_shared.pathmap import map_path

from .proc import run

log = logging.getLogger(__name__)

CHUNK_BYTES = 768 * 1024  # raw bytes per chunk; base64 keeps messages far below the socket limit
ENCODE_TIMEOUT = 300.0
EXTRACT_TIMEOUT = 60.0

Send = Callable[[str, Any], Awaitable[Any]]


class PreviewRunner:
    def __init__(self, send: Send, state_dir: Path, path_mappings: Callable[[], list[P.PathMapping]]) -> None:
        self._send = send
        self._dir = state_dir / "previews"
        self._mappings = path_mappings
        self._task: asyncio.Task | None = None
        self._request_id: str | None = None

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, req: P.PreviewRequest) -> None:
        if self.busy:
            asyncio.create_task(self._send(P.NODE_PREVIEW_RESULT, P.PreviewResult(request_id=req.request_id, ok=False, error="This node is already making a preview")))
            return
        self._request_id = req.request_id
        self._task = asyncio.create_task(self._guard(req), name=f"preview-{req.request_id[:8]}")

    def cancel(self, request_id: str) -> None:
        if self.busy and self._request_id == request_id and self._task is not None:
            self._task.cancel()

    async def shutdown(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def _guard(self, req: P.PreviewRequest) -> None:
        work = self._dir / req.request_id
        try:
            result = await self._run(req, work)
        except asyncio.CancelledError:
            log.info("Preview %s cancelled", req.request_id[:8])
            return
        except Exception as exc:  # report instead of dying silently
            log.exception("Preview %s failed", req.request_id[:8])
            result = P.PreviewResult(request_id=req.request_id, ok=False, error=f"The node hit an unexpected error: {exc}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        await self._send(P.NODE_PREVIEW_RESULT, result)

    async def _progress(self, req: P.PreviewRequest, done: int, total: int, message: str) -> None:
        await self._send(P.NODE_PREVIEW_PROGRESS, P.PreviewProgress(request_id=req.request_id, done=done, total=total, message=message))

    def _build(self, req: P.PreviewRequest, src: str, work: Path, hw_decode: bool) -> PreviewBuild:
        choice = req.encoder.model_copy(update={"hw_decode": req.encoder.hw_decode and hw_decode})
        return build_preview_commands(req.source_media, req.profile, choice, src, str(work), req.positions, req.sample_seconds, req.preroll_seconds)

    async def _run(self, req: P.PreviewRequest, work: Path) -> P.PreviewResult:
        def failed(error: str) -> P.PreviewResult:
            return P.PreviewResult(request_id=req.request_id, ok=False, error=error)

        src = map_path(req.source_path, self._mappings())
        if not os.path.isfile(src):
            mapped = f" (looked for it at {src} on this node)" if src != req.source_path else ""
            return failed(f"This node can't see {req.source_path}{mapped}. Check its volume mounts or path mappings.")
        work.mkdir(parents=True, exist_ok=True)
        try:
            build = self._build(req, src, work, hw_decode=True)
        except BuildError as exc:
            return failed(str(exc))

        total = len(build.steps)
        samples: list[P.PreviewSample] = []
        notes = list(build.notes)
        hw_retry_used = False
        for i, step in enumerate(build.steps):
            await self._progress(req, i, total, f"Encoding sample {i + 1} of {total}")
            res = await run(step.encode, timeout=ENCODE_TIMEOUT)
            if res.returncode != 0 and req.encoder.hw_decode and not hw_retry_used:
                # Same fallback as real jobs: retry without hardware decoding.
                hw_retry_used = True
                build = self._build(req, src, work, hw_decode=False)
                step = build.steps[i]
                notes.append("Hardware decoding failed for this file; the preview used software decoding, as a job would.")
                res = await run(step.encode, timeout=ENCODE_TIMEOUT)
            if res.returncode != 0:
                return failed(f"Encoding sample {i + 1} failed: {_last_line(res.stderr, res.returncode)}")
            for args in (step.extract_original, step.extract_encoded):
                res = await run(args, timeout=EXTRACT_TIMEOUT)
                if res.returncode != 0:
                    return failed(f"Couldn't grab a frame for sample {i + 1}: {_last_line(res.stderr, res.returncode)}")
            for name in (step.original_png, step.encoded_png):
                await self._send_file(req.request_id, work / name, name)
            samples.append(
                P.PreviewSample(
                    index=i,
                    position=step.position,
                    original=step.original_png,
                    encoded=step.encoded_png,
                    width=build.width,
                    height=build.height,
                    encoded_bytes=os.path.getsize(step.sample_path),
                    sample_seconds=step.sample_seconds,
                )
            )
            os.remove(step.sample_path)
        await self._progress(req, total, total, "Done")
        return P.PreviewResult(request_id=req.request_id, ok=True, samples=samples, encoder=req.encoder.encoder, quality_label=build.quality_label, notes=notes)

    async def _send_file(self, request_id: str, path: Path, name: str) -> None:
        data = await asyncio.to_thread(path.read_bytes)
        offset = 0
        while True:
            part = data[offset : offset + CHUNK_BYTES]
            last = offset + len(part) >= len(data)
            await self._send(P.NODE_PREVIEW_CHUNK, P.PreviewChunk(request_id=request_id, name=name, offset=offset, data=base64.b64encode(part).decode(), last=last))
            offset += len(part)
            if last:
                break


def _last_line(stderr: str, code: int) -> str:
    lines = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else f"exit code {code}")[:300]

