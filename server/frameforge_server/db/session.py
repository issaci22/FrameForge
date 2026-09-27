"""Engine/session factory and startup migrations."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..config import Settings

log = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=10000")
    cur.close()


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _sessionmaker
    url = settings.db_url
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"timeout": 30}}
    _engine = create_async_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(_engine.sync_engine, "connect", _sqlite_pragmas)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def alembic_config(settings: Settings) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", settings.sync_db_url)
    return cfg


def run_migrations(settings: Settings) -> None:
    log.info("Applying database migrations")
    command.upgrade(alembic_config(settings), "head")


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database not initialised")
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with sessionmaker()() as session:
        yield session


async def dispose() -> None:
    if _engine is not None:
        await _engine.dispose()
