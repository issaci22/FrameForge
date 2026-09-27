"""FastAPI application factory and process lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select

from frameforge_shared import __version__

from .api import auth, files, jobs, libraries, nodes, previews, profiles, retention, rules, sockets, system
from .auth.sessions import purge_expired_sessions
from .config import get_settings
from .db import session as db_session
from .db.models import TERMINAL_JOB_STATES, Job
from .db.types import utcnow
from .logging_setup import configure_logging
from .services import jobs as job_service
from .services.node_manager import manager
from .services.presets import seed_profiles
from .services.previews import previews as preview_service
from .services.retention import retention_loop
from .services.scanner import scan_loop
from .services.scheduler import scheduler
from .services.system_settings import load_settings

log = logging.getLogger("frameforge")

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CLIENT_HEADER = "x-frameforge-client"
MAINTENANCE_INTERVAL = 3600


async def _maintenance_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await purge_expired_sessions()
            preview_service.expire()
            settings = await load_settings()
            cutoff = utcnow() - timedelta(days=settings.job_history_days)
            async with db_session.sessionmaker()() as db:
                ids = list((await db.execute(select(Job.id).where(Job.state.in_(TERMINAL_JOB_STATES), Job.finished_at < cutoff))).scalars())
                if ids:
                    await db.execute(delete(Job).where(Job.id.in_(ids)))
                    await db.commit()
                    for job_id in ids:
                        job_service.cleanup_job_log(job_id)
                    log.info("Removed %d jobs older than %d days", len(ids), settings.job_history_days)
        except Exception:
            log.exception("Maintenance failed")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=MAINTENANCE_INTERVAL)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.ensure_dirs()
    preview_service.reset_cache()
    configure_logging(settings.logs_dir, settings.log_level)
    log.info("FrameForge %s starting (role=%s, config=%s)", __version__, settings.role, settings.config_dir)
    await asyncio.to_thread(db_session.run_migrations, settings)
    db_session.init_engine(settings)
    async with db_session.sessionmaker()() as db:
        await seed_profiles(db)

    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(scheduler.run(stop), name="scheduler"),
        asyncio.create_task(scan_loop(stop), name="scan-loop"),
        asyncio.create_task(_maintenance_loop(stop), name="maintenance"),
        asyncio.create_task(retention_loop(stop), name="retention"),
    ]
    agent = None
    if settings.role == "all":
        from frameforge_node.agent import NodeAgent

        token = await manager.ensure_local_node()
        agent = NodeAgent(server_url=f"http://127.0.0.1:{settings.port}", token=token, state_dir=settings.config_dir / "node", embedded=True)
        tasks.append(asyncio.create_task(agent.run(stop), name="local-node"))
    try:
        yield
    finally:
        log.info("Shutting down")
        stop.set()
        if agent is not None:
            await agent.shutdown()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(BaseException):
                await t
        await db_session.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="FrameForge", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):  # noqa: ANN001, ANN202
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/") and CLIENT_HEADER not in request.headers:
            return JSONResponse({"detail": f"Missing {CLIENT_HEADER} header"}, status_code=403)
        return await call_next(request)

    api = APIRouter(prefix="/api/v1")
    for module in (auth, libraries, files, jobs, nodes, previews, profiles, retention, rules, system, sockets):
        api.include_router(module.router)
    app.include_router(api)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"ok": True, "version": __version__}

    _mount_web(app, settings.web_dir)
    return app


def _mount_web(app: FastAPI, web_dir: Path) -> None:
    index = web_dir / "index.html"
    if (web_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):  # noqa: ANN202
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        candidate = (web_dir / full_path).resolve()
        if full_path and candidate.is_file() and web_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h1>FrameForge</h1><p>The web UI has not been built. The API is available at <a href='/api/docs'>/api/docs</a>.</p>")


app = create_app()
