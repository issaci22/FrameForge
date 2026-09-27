"""``python -m frameforge_node`` — run a standalone transcoding node (FF_ROLE=node)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from .agent import NodeAgent


def _configure_logging() -> None:
    logging.basicConfig(level=os.environ.get("FF_LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def _main() -> int:
    server = os.environ.get("FF_SERVER_URL", "").strip()
    token = os.environ.get("FF_NODE_TOKEN", "").strip()
    if not server or not token:
        logging.error("FF_SERVER_URL and FF_NODE_TOKEN must be set. Create the node in the FrameForge web UI (Nodes → Add node) to get them.")
        return 2
    state_dir = Path(os.environ.get("FF_CONFIG_DIR", "/config"))
    agent = NodeAgent(server_url=server, token=token, state_dir=state_dir)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows dev
            pass
    runner = asyncio.create_task(agent.run(stop))
    await stop.wait()
    logging.info("Stopping node (waiting for jobs that are finalizing)…")
    await agent.shutdown()
    runner.cancel()
    return 0


async def _detect_report() -> int:
    """``--detect``: run the real capability tests once and print what this machine can do."""
    from .capabilities import detect

    caps = await detect(Path(os.environ.get("FF_CONFIG_DIR", "/config")))
    out = [f"FFmpeg:  {caps.ffmpeg_version or 'not found'}", f"CPU:     {caps.cpu_model} ({caps.cpu_threads} threads)", "", "GPUs:"]
    out += [f"  [{g.index}] {g.vendor:<7} {g.name}  {g.device or ''}" for g in caps.gpus] or ["  none visible"]
    out.append(f"VA-API/QSV device used: {caps.render_device or '-'}")
    out += ["", "Encoders:"]
    for e in sorted(caps.encoders, key=lambda e: (e.backend, e.codec)):
        status = "OK  " if e.verified else "FAIL"
        out.append(f"  {status} {e.backend:<6} {e.codec:<5} {e.name:<12} {'' if e.verified else (e.error or '')}")
    out += ["", "Hardware decoders:"]
    for d in sorted(caps.decoders, key=lambda d: (d.backend, d.codec)):
        out.append(f"  {'OK  ' if d.verified else 'FAIL'} {d.backend:<6} {d.codec:<5} {'' if d.verified else (d.error or '')}")
    if not caps.decoders:
        out.append("  none tested")
    out += [""] + [f"Note: {n}" for n in caps.notes]
    sys.stdout.write("\n".join(out).rstrip() + "\n")
    return 0 if caps.engines.get("ffmpeg") else 1


def main() -> None:
    _configure_logging()
    if "--detect" in sys.argv[1:]:
        sys.exit(asyncio.run(_detect_report()))
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
