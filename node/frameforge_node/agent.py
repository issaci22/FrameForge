"""Node agent: keeps a websocket to the server, runs jobs, streams metrics and logs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from frameforge_shared import PROTOCOL_VERSION, __version__
from frameforge_shared import protocol as P

from .capabilities import detect
from .metrics import MetricsSampler
from .preview import PreviewRunner
from .runner import JobRunner

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 2.0
NODE_LOG_FLUSH_SECONDS = 2.0
MAX_BACKOFF = 30.0
MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class _LogForwarder(logging.Handler):
    """Buffers node log records so they can be shipped to the server."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.lines) < 5000:
            self.lines.append(self.format(record))

    def drain(self) -> list[str]:
        out, self.lines = self.lines, []
        return out


class NodeAgent:
    def __init__(self, server_url: str, token: str, state_dir: Path, embedded: bool = False) -> None:
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.state_dir = state_dir
        self.embedded = embedded
        self.caps: P.NodeCapabilities | None = None
        self.sampler: MetricsSampler | None = None
        self.ws: ClientConnection | None = None
        self.node_name: str | None = None
        self._send_lock = asyncio.Lock()
        self._pending_results: dict[int, P.JobResult] = {}
        self.runner = JobRunner(self._send, self._send_result)
        self.previews = PreviewRunner(self._send, state_dir, lambda: self.runner.path_mappings)
        self._log_forwarder = _LogForwarder()
        logging.getLogger("frameforge_node").addHandler(self._log_forwarder)

    @property
    def ws_url(self) -> str:
        base = self.server_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{base}/api/v1/node/connect"

    # ------------------------------------------------------------------ sending

    async def _send(self, msg_type: str, data: BaseModel | dict[str, Any]) -> bool:
        ws = self.ws
        if ws is None:
            return False
        try:
            async with self._send_lock:
                await ws.send(json.dumps(P.envelope(msg_type, data)))
            return True
        except ConnectionClosed:
            return False

    async def _send_result(self, result: P.JobResult) -> None:
        # Results are precious: keep them until they've been delivered.
        self._pending_results[result.job_id] = result
        if await self._send(P.NODE_JOB_RESULT, result):
            self._pending_results.pop(result.job_id, None)
        log.info("Job %s finished: %s", result.job_id, result.status if not result.diagnosis else f"{result.status} ({result.diagnosis.title})")

    # ------------------------------------------------------------------ lifecycle

    async def run(self, stop: asyncio.Event) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log.info("Detecting hardware capabilities…")
        self.caps = await detect(self.state_dir)
        self.sampler = MetricsSampler(self.caps)
        backoff = 1.0
        while not stop.is_set():
            try:
                await self._session(stop)
                backoff = 1.0
            except InvalidStatus as exc:
                log.error("Server refused the connection (HTTP %s). Check FF_SERVER_URL.", exc.response.status_code)
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as exc:
                if not self.embedded or backoff > 2:
                    log.warning("Can't reach FrameForge server at %s (%s); retrying in %.0fs", self.server_url, exc, backoff)
            except _Fatal as exc:
                log.error("%s", exc)
                backoff = MAX_BACKOFF
            except Exception:
                log.exception("Node connection error")
            self.ws = None
            if stop.is_set():
                break
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=backoff + random.uniform(0, 1))
            backoff = min(MAX_BACKOFF, backoff * 2)

    async def shutdown(self) -> None:
        await self.previews.shutdown()
        await self.runner.shutdown()

    async def _session(self, stop: asyncio.Event) -> None:
        assert self.caps is not None
        async with connect(
            self.ws_url,
            additional_headers={"Authorization": f"Bearer {self.token}"},
            max_size=MAX_MESSAGE_BYTES,
            ping_interval=20,
            ping_timeout=30,
            open_timeout=15,
        ) as ws:
            self.ws = ws
            active = sorted(set(self.runner.active_ids) | set(self._pending_results))
            await self._send(
                P.NODE_HELLO,
                P.Hello(protocol=PROTOCOL_VERSION, node_version=__version__, capabilities=self.caps, active_jobs=active, features=list(P.NODE_FEATURES)),
            )
            welcome = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if welcome.get("type") == P.SERVER_ERROR:
                raise _Fatal(welcome.get("data", {}).get("message", "Server rejected this node"))
            if welcome.get("type") != P.SERVER_WELCOME:
                raise _Fatal(f"Unexpected first message from server: {welcome.get('type')}")
            w = P.Welcome.model_validate(welcome["data"])
            self.node_name = w.name
            self.runner.max_concurrency = w.max_concurrency
            self.runner.path_mappings = w.path_mappings
            log.info("Connected to FrameForge as node '%s' (max %d concurrent jobs)", w.name, w.max_concurrency)

            for result in list(self._pending_results.values()):
                if await self._send(P.NODE_JOB_RESULT, result):
                    self._pending_results.pop(result.job_id, None)

            tasks = [asyncio.create_task(self._heartbeat()), asyncio.create_task(self._forward_logs())]
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await self._dispatch(msg)
                    if stop.is_set():
                        break
            finally:
                for t in tasks:
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t

    async def _heartbeat(self) -> None:
        assert self.sampler is not None
        while True:
            metrics = await self.sampler.sample()
            await self._send(P.NODE_HEARTBEAT, P.Heartbeat(metrics=metrics, active_jobs=sorted(set(self.runner.active_ids) | set(self._pending_results))))
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def _forward_logs(self) -> None:
        while True:
            await asyncio.sleep(NODE_LOG_FLUSH_SECONDS)
            lines = self._log_forwarder.drain()
            if lines:
                await self._send(P.NODE_LOG, P.NodeLog(lines=lines))

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        data = msg.get("data") or {}
        try:
            if mtype == P.SERVER_ASSIGN:
                a = P.JobAssignment.model_validate(data)
                log.info("Received job %s: %s", a.job_id, a.source_path)
                self.runner.start(a)
            elif mtype == P.SERVER_CANCEL:
                await self.runner.cancel(P.CancelJob.model_validate(data).job_id)
            elif mtype == P.SERVER_RECOVER:
                rec = P.RecoverJob.model_validate(data)
                log.warning("Recovering interrupted finalize for job %s", rec.job_id)
                self.runner.recover(rec)
            elif mtype == P.SERVER_CONFIG:
                cfg = P.NodeConfig.model_validate(data)
                self.runner.max_concurrency = cfg.max_concurrency
                self.runner.path_mappings = cfg.path_mappings
                log.info("Settings updated: max %d concurrent jobs, %d path mapping(s)", cfg.max_concurrency, len(cfg.path_mappings))
            elif mtype == P.SERVER_REDETECT:
                log.info("Re-detecting capabilities on request")
                self.caps = await detect(self.state_dir)
                self.sampler = MetricsSampler(self.caps)
                await self._send(P.NODE_CAPABILITIES, self.caps)
            elif mtype == P.SERVER_PREVIEW:
                req = P.PreviewRequest.model_validate(data)
                log.info("Making a compression preview of %s", req.source_path)
                self.previews.start(req)  # runs in its own task; never blocks this loop
            elif mtype == P.SERVER_PREVIEW_CANCEL:
                self.previews.cancel(P.PreviewCancel.model_validate(data).request_id)
            elif mtype == P.SERVER_ERROR:
                log.error("Server: %s", data.get("message"))
        except ValidationError as exc:
            log.error("Invalid %s message from server: %s", mtype, exc)


class _Fatal(Exception):
    pass
