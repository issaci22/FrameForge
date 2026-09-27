"""Small subprocess helper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


async def run(args: list[str], timeout: float = 30.0) -> ProcResult:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL)
    except (FileNotFoundError, PermissionError) as exc:
        return ProcResult(127, "", str(exc))
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return ProcResult(-1, "", f"timed out after {timeout:.0f}s", timed_out=True)
    except asyncio.CancelledError:
        if proc.returncode is None:  # don't leave the child running when our task is cancelled
            proc.kill()
            await asyncio.shield(proc.wait())
        raise
    return ProcResult(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))
