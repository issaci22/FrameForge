"""Settings, statistics, system info, folder browser, logs."""

from __future__ import annotations

import asyncio
import os
import platform
import posixpath

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared import PROTOCOL_VERSION, __version__
from frameforge_shared.tools import ffmpeg_path

from ..auth.sessions import require_user
from ..config import get_settings
from ..db.session import get_db
from ..logging_setup import tail_file
from ..services import stats
from ..services.system_settings import GeneralSettings, load_settings, save_settings

router = APIRouter(tags=["system"], dependencies=[Depends(require_user)])

_ffmpeg_version: str | None = None


async def _ffmpeg_version_str() -> str | None:
    global _ffmpeg_version
    if _ffmpeg_version is None:
        try:
            proc = await asyncio.create_subprocess_exec(ffmpeg_path(), "-hide_banner", "-version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            first = out.decode(errors="replace").splitlines()
            _ffmpeg_version = first[0] if first else "unknown"
        except FileNotFoundError:
            _ffmpeg_version = "not installed"
    return _ffmpeg_version


@router.get("/settings")
async def get_general(db: AsyncSession = Depends(get_db)) -> dict:
    return (await load_settings(db)).model_dump(mode="json")


@router.put("/settings")
async def put_general(body: GeneralSettings, db: AsyncSession = Depends(get_db)) -> dict:
    return (await save_settings(db, body)).model_dump(mode="json")


@router.get("/stats/overview")
async def stats_overview(db: AsyncSession = Depends(get_db)) -> dict:
    return await stats.overview(db)


@router.get("/stats/history")
async def stats_history(days: int = Query(30, ge=1, le=365), db: AsyncSession = Depends(get_db)) -> list[dict]:
    return await stats.history(db, days)


@router.get("/system/info")
async def system_info() -> dict:
    s = get_settings()
    return {
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
        "role": s.role,
        "database": "postgres" if s.db_url.startswith("postgres") else "sqlite",
        "ffmpeg": await _ffmpeg_version_str(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config_dir": str(s.config_dir),
        "engines": {"ffmpeg": {"available": True}, "handbrake": {"available": False, "reason": "Not included in this version"}},
    }


@router.get("/system/browse")
async def browse(path: str = "/") -> dict:
    """List sub-folders so users can pick library paths without guessing container paths."""
    path = posixpath.normpath(path.replace("\\", "/")) if path else "/"
    if not path.startswith("/"):
        raise HTTPException(400, "Path must be absolute")

    def _list() -> list[dict]:
        entries = []
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith(".") or not e.is_dir(follow_symlinks=True):
                    continue
                entries.append({"name": e.name, "path": posixpath.join(path, e.name)})
        return sorted(entries, key=lambda x: x["name"].lower())

    try:
        dirs = await asyncio.to_thread(_list)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Folder not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, "Permission denied") from exc
    parent = posixpath.dirname(path) if path != "/" else None
    return {"path": path, "parent": parent, "dirs": dirs[:500]}


@router.get("/system/logs", response_class=PlainTextResponse)
async def server_logs(lines: int = Query(500, ge=10, le=20000)) -> str:
    return "\n".join(await asyncio.to_thread(tail_file, get_settings().logs_dir / "server.log", lines))
