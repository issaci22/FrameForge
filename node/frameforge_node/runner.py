"""Run assigned jobs: prepare → transcode → validate → finalize, reporting as we go."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from frameforge_shared import protocol as P
from frameforge_shared.errors import diagnose, diagnosis, is_decode_related
from frameforge_shared.ffmpeg_builder import BuildError, build_command
from frameforge_shared.fingerprint import fingerprint_file
from frameforge_shared.pathmap import map_path, unmap_path
from frameforge_shared.probe import ProbeError, probe_file
from frameforge_shared.progress import ProgressParser

from . import finalizer
from .validator import SIZE_CHECK, validate

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 1.0
LOG_FLUSH_INTERVAL = 1.0
STALL_TIMEOUT = 600  # seconds without any FFmpeg progress output
STDERR_TAIL = 400
FREE_SPACE_MARGIN = 1024**3

Send = Callable[[str, Any], Awaitable[None]]


def _validation_diagnosis(failed: list[P.ValidationCheck]) -> P.Diagnosis:
    """Explain a rejected output. A size-only rejection gets its own diagnosis: it is expected, not a fault."""
    if [c.name for c in failed] == [SIZE_CHECK]:
        return diagnosis(
            "output_larger",
            "The new file was bigger than the original",
            f"Keeping it would use more space, not less, so FrameForge discarded it and left the original untouched. {failed[0].detail}.",
            "The source is already efficiently compressed (low bitrate, or already H.265/AV1)",
            "The profile's quality setting is higher than this source needs",
            "Hardware encoders need more bits than software encoders for the same quality",
            "To keep larger outputs anyway, raise the size limit or turn off rejection in the library's safety settings",
        )
    return diagnosis(
        "validation_failed",
        "The output didn't pass validation",
        "FrameForge discarded the output and left the original untouched. " + "; ".join(f"{c.name}: {c.detail}" for c in failed),
        "The encode was cut short (e.g. a corrupt section in the source)",
        "A validation threshold is too strict for this kind of file (see the library's safety settings)",
    )


@dataclass
class RunningJob:
    job_id: int
    task: asyncio.Task | None = None
    proc: asyncio.subprocess.Process | None = None
    cancelled: bool = False
    finalizing: bool = False
    stderr_tail: deque[str] = field(default_factory=lambda: deque(maxlen=STDERR_TAIL))


class JobRunner:
    def __init__(self, send: Send, send_result: Callable[[P.JobResult], Awaitable[None]]) -> None:
        self._send = send
        self._send_result = send_result
        self.jobs: dict[int, RunningJob] = {}
        self.max_concurrency = 1
        self.path_mappings: list[P.PathMapping] = []

    @property
    def active_ids(self) -> list[int]:
        return sorted(self.jobs)

    # ------------------------------------------------------------------ control

    def start(self, assignment: P.JobAssignment) -> None:
        if assignment.job_id in self.jobs:
            return
        if len(self.jobs) >= self.max_concurrency:
            asyncio.create_task(self._send_result(P.JobResult(job_id=assignment.job_id, status="rejected", reject_reason="Node is at its concurrency limit")))
            return
        rj = RunningJob(assignment.job_id)
        self.jobs[assignment.job_id] = rj
        rj.task = asyncio.create_task(self._guard(rj, self._run(rj, assignment)), name=f"job-{assignment.job_id}")

    def recover(self, rec: P.RecoverJob) -> None:
        if rec.job_id in self.jobs:
            return
        rj = RunningJob(rec.job_id, finalizing=True)
        self.jobs[rec.job_id] = rj
        rj.task = asyncio.create_task(self._guard(rj, self._recover(rj, rec)), name=f"recover-{rec.job_id}")

    async def cancel(self, job_id: int) -> None:
        rj = self.jobs.get(job_id)
        if rj is None:
            return
        if rj.finalizing:
            log.warning("Ignoring cancel for job %s: already finalizing", job_id)
            return
        rj.cancelled = True
        await self._kill(rj)

    async def shutdown(self) -> None:
        for rj in list(self.jobs.values()):
            if not rj.finalizing:
                rj.cancelled = True
                await self._kill(rj)
        tasks = [rj.task for rj in self.jobs.values() if rj.task]
        if tasks:
            await asyncio.wait(tasks, timeout=30)

    async def _kill(self, rj: RunningJob) -> None:
        proc = rj.proc
        if proc and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()

    async def _guard(self, rj: RunningJob, coro: Awaitable[P.JobResult]) -> None:
        try:
            result = await coro
        except Exception as exc:  # last-resort safety net: report instead of silently dying
            log.exception("Job %s crashed", rj.job_id)
            result = P.JobResult(job_id=rj.job_id, status="failed", diagnosis=diagnosis("node_error", "The node hit an unexpected error", str(exc), "See the node log for details"))
        finally:
            self.jobs.pop(rj.job_id, None)
        await self._send_result(result)

    # ------------------------------------------------------------------ helpers

    async def _stage(self, job_id: int, stage: str, message: str, **data: Any) -> None:
        await self._send(P.NODE_JOB_STAGE, P.JobStage(job_id=job_id, stage=stage, message=message, data=data))  # type: ignore[arg-type]

    async def _log(self, job_id: int, lines: list[str]) -> None:
        if lines:
            await self._send(P.NODE_JOB_LOG, P.JobLogLines(job_id=job_id, lines=lines))

    def _local_plan(self, plan: P.FinalizePlan) -> P.FinalizePlan:
        m = self.path_mappings
        return P.FinalizePlan(
            source=map_path(plan.source, m),
            temp_output=map_path(plan.temp_output, m),
            final_output=map_path(plan.final_output, m),
            original_action=plan.original_action,
            backup_path=map_path(plan.backup_path, m) if plan.backup_path else None,
        )

    def _server_path(self, path: str) -> str:
        """Node path → server path, so job events name files the way the user's library does."""
        return unmap_path(path, self.path_mappings)

    @staticmethod
    def _cleanup_temp(path: str) -> None:
        with contextlib.suppress(OSError):
            os.remove(path)
        with contextlib.suppress(OSError):
            os.rmdir(os.path.dirname(path))

    def _verifier(self, job_id: int) -> Callable[[str], Awaitable[bool]]:
        async def verify(path: str) -> bool:
            try:
                info = await probe_file(path)
            except ProbeError:
                return False
            tag = info.frameforge_tag or ""
            return info.video is not None and f"job={job_id};" in tag + ";"

        return verify

    # ------------------------------------------------------------------ main flow

    async def _run(self, rj: RunningJob, a: P.JobAssignment) -> P.JobResult:
        job_id = a.job_id
        started = time.monotonic()
        plan = self._local_plan(a.plan)
        src = plan.source
        notes: list[str] = []

        def fail(diag: P.Diagnosis, **extra: Any) -> P.JobResult:
            return P.JobResult(job_id=job_id, status="failed", diagnosis=diag, notes=notes, duration_seconds=time.monotonic() - started, **extra)

        await self._stage(job_id, "preparing", "Checking the source file")
        if not os.path.isfile(src):
            mapped = f" (looked for it at {src} on this node)" if src != a.source_path else ""
            return P.JobResult(job_id=job_id, status="rejected", reject_reason=f"This node can't see {a.source_path}{mapped}. Check the node's volume mounts or path mappings.")
        st = os.stat(src)
        if a.source_media and a.source_media.size and abs(st.st_size - a.source_media.size) > 0:
            return fail(diagnosis("source_changed", "The source changed since it was scanned", "The file's size is different from when FrameForge analyzed it, so it may still be recording or copying.", "Wait for the recording/copy to finish; the next scan will pick it up again"))

        tmp_dir = os.path.dirname(plan.temp_output)
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            free = shutil.disk_usage(tmp_dir).free
        except OSError as exc:
            return fail(diagnose([str(exc)], None))
        if free < min(st.st_size, 50 * 1024**3) * 0.6 + FREE_SPACE_MARGIN:
            return fail(diagnosis("disk_full", "Not enough free space", f"Only {free / 1024**3:.1f} GB free next to the output; this job needs room for a new copy of a {st.st_size / 1024**3:.1f} GB file.", "Free up space or point the library's output to another disk"))

        # A plan that can't finish (e.g. an earlier output already sits at the output path) fails now,
        # not after hours of encoding.
        try:
            finalizer.preflight(plan, self._server_path)
        except finalizer.FinalizeError as exc:
            self._cleanup_temp(plan.temp_output)
            return fail(exc.diagnosis)

        try:
            info = await probe_file(src)
        except ProbeError as exc:
            return fail(diagnose([str(exc)], None) if "Invalid data" in str(exc) else diagnosis("probe_failed", "Couldn't analyze the source", str(exc), "The file may be damaged or still being written"))

        choice = a.encoder
        force_sw = False
        for attempt in (1, 2):
            try:
                built = build_command(info, a.profile, choice, src, plan.temp_output, a.frameforge_tag, force_software_decode=force_sw)
            except BuildError as exc:
                return fail(diagnosis("build_failed", "This file can't be converted with this profile", str(exc)))
            if attempt == 1:
                notes.extend(built.notes)
            hw_dec = choice.hw_decode and not force_sw
            await self._log(job_id, [f"$ {built.command_line()}"] + [f"# {n}" for n in built.notes])
            await self._stage(
                job_id,
                "transcoding",
                f"Encoding with {choice.encoder}" + (" (hardware decode)" if hw_dec else "") + (" — retry with software decoding" if force_sw else ""),
                encoder=choice.encoder,
                hw_decode=hw_dec,
                pipeline=built.pipeline,
            )
            rc = await self._ffmpeg(rj, built.args, info.duration)
            if rj.cancelled:
                self._cleanup_temp(plan.temp_output)
                return P.JobResult(job_id=job_id, status="cancelled", duration_seconds=time.monotonic() - started)
            if rc == 0:
                break
            diag = diagnose(list(rj.stderr_tail), rc)
            self._cleanup_temp(plan.temp_output)
            if attempt == 1 and hw_dec and (is_decode_related(diag) or diag.code in ("ffmpeg_failed", "pixel_format")):
                force_sw = True
                rj.stderr_tail.clear()
                await self._stage(job_id, "transcoding", "Hardware decoding failed for this file; retrying with software decoding", level="warn")
                continue
            return fail(diag, encoder_used=choice.encoder, hw_decode_used=hw_dec)

        # ---- validate ------------------------------------------------------
        await self._stage(job_id, "validating", "Checking the output")
        report, out_info = await validate(info, st.st_size, plan.temp_output, a.thresholds, a.profile)
        for c in report.checks:
            if not c.passed and c.severity == "warning":
                notes.append(f"{c.name}: {c.detail}")
        if not report.passed:
            self._cleanup_temp(plan.temp_output)
            failed = [c for c in report.checks if not c.passed and c.severity == "error"]
            return fail(
                _validation_diagnosis(failed),
                validation=report,
                encoder_used=choice.encoder,
                hw_decode_used=choice.hw_decode and not force_sw,
            )

        # Keep the original's timestamps so age-based rules keep working.
        with contextlib.suppress(OSError):
            os.utime(plan.temp_output, (st.st_atime, st.st_mtime))
        with contextlib.suppress(OSError):
            os.chmod(plan.temp_output, st.st_mode & 0o7777)
        with contextlib.suppress(OSError):
            os.chown(plan.temp_output, st.st_uid, st.st_gid)

        # ---- finalize --------------------------------------------------------
        rj.finalizing = True
        await self._stage(job_id, "finalizing", "Putting the new file in place")

        async def on_step(msg: str) -> None:
            await self._stage(job_id, "finalizing", msg)

        try:
            outcome = await finalizer.finalize(plan, on_step, self._verifier(job_id), self._server_path)
        except finalizer.FinalizeError as exc:
            self._cleanup_temp(plan.temp_output)
            return fail(exc.diagnosis, validation=report, encoder_used=choice.encoder)
        return await self._completed(job_id, a.plan, outcome, st.st_size, out_info, report, notes, started, choice.encoder, choice.hw_decode and not force_sw)

    async def _completed(
        self,
        job_id: int,
        server_plan: P.FinalizePlan,
        outcome: finalizer.FinalizeOutcome,
        source_size: int,
        out_info: Any,
        report: P.ValidationReport | None,
        notes: list[str],
        started: float,
        encoder: str | None,
        hw_decode: bool | None,
    ) -> P.JobResult:
        notes = notes + outcome.warnings
        fp = None
        with contextlib.suppress(OSError):
            fp = await asyncio.to_thread(fingerprint_file, outcome.final_path)
        original_size = original_fp = None
        if outcome.original_path and outcome.original_disposition in ("backed_up", "kept", "rescued"):
            with contextlib.suppress(OSError):
                original_size = os.path.getsize(outcome.original_path)
                original_fp = await asyncio.to_thread(fingerprint_file, outcome.original_path)
        return P.JobResult(
            job_id=job_id,
            status="completed",
            validation=report,
            source_size=source_size,
            output_size=outcome.output_size,
            output_media=out_info,
            final_path=server_plan.final_output,
            output_fingerprint=fp,
            encoder_used=encoder,
            hw_decode_used=hw_decode,
            notes=notes,
            duration_seconds=time.monotonic() - started,
            original_disposition=outcome.original_disposition,
            original_path=self._server_path(outcome.original_path) if outcome.original_path else None,
            original_size=original_size,
            original_fingerprint=original_fp,
        )

    async def _recover(self, rj: RunningJob, rec: P.RecoverJob) -> P.JobResult:
        started = time.monotonic()
        plan = self._local_plan(rec.plan)

        async def on_step(msg: str) -> None:
            await self._stage(rec.job_id, "finalizing", msg)

        try:
            outcome = await finalizer.recover(plan, on_step, self._verifier(rec.job_id), self._server_path)
        except finalizer.FinalizeError as exc:
            return P.JobResult(job_id=rec.job_id, status="failed", diagnosis=exc.diagnosis)
        out_info = None
        with contextlib.suppress(ProbeError):
            out_info = await probe_file(outcome.final_path)
        size_hint = None
        with contextlib.suppress(OSError):
            where = outcome.original_path or plan.backup_path
            size_hint = os.path.getsize(where) if where and os.path.exists(where) else None
        return await self._completed(rec.job_id, rec.plan, outcome, size_hint or 0, out_info, None, ["Recovered after an interruption"], started, None, None)

    # ------------------------------------------------------------------ ffmpeg

    async def _ffmpeg(self, rj: RunningJob, args: list[str], duration: float) -> int:
        try:
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
        except FileNotFoundError:
            rj.stderr_tail.append("ffmpeg: No such file or directory (FFmpeg is not installed)")
            return 127
        rj.proc = proc
        parser = ProgressParser()
        started = time.monotonic()
        last_output = time.monotonic()
        pending_log: list[str] = []

        async def read_progress() -> None:
            nonlocal last_output
            last_sent = 0.0
            assert proc.stdout
            async for raw in proc.stdout:
                last_output = time.monotonic()
                snap = parser.feed(raw.decode(errors="replace"))
                if snap is None:
                    continue
                now = time.monotonic()
                if now - last_sent < PROGRESS_INTERVAL and not snap.ended:
                    continue
                last_sent = now
                out_t = snap.out_time_s or 0.0
                pct = min(99.9, out_t / duration * 100) if duration > 0 else 0.0
                eta = (duration - out_t) / snap.speed if snap.speed and duration > 0 else None
                await self._send(
                    P.NODE_JOB_PROGRESS,
                    P.JobProgress(job_id=rj.job_id, percent=round(pct, 2), frame=snap.frame, fps=snap.fps, speed=snap.speed, bitrate_kbps=snap.bitrate_kbps, out_size=snap.total_size, elapsed=now - started, eta=eta),
                )

        async def read_stderr() -> None:
            nonlocal last_output
            assert proc.stderr
            async for raw in proc.stderr:
                last_output = time.monotonic()
                line = raw.decode(errors="replace").rstrip()
                if not line:
                    continue
                rj.stderr_tail.append(line)
                pending_log.append(line)

        async def flush_logs() -> None:
            while True:
                await asyncio.sleep(LOG_FLUSH_INTERVAL)
                if pending_log:
                    batch = pending_log[:]
                    pending_log.clear()
                    await self._log(rj.job_id, batch)
                if time.monotonic() - last_output > STALL_TIMEOUT:
                    rj.stderr_tail.append(f"FrameForge: no output from FFmpeg for {STALL_TIMEOUT}s; stopping the stalled process")
                    await self._kill(rj)
                    return

        readers = asyncio.gather(read_progress(), read_stderr())
        flusher = asyncio.create_task(flush_logs())
        try:
            await readers
            rc = await proc.wait()
        finally:
            flusher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await flusher
            if pending_log:
                await self._log(rj.job_id, pending_log[:])
            rj.proc = None
        return rc
