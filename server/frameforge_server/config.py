"""Process-level settings. Keep this small: everything user-facing is configured in the UI."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FF_", env_file=None, extra="ignore")

    role: Literal["all", "server", "node"] = "all"
    host: str = "0.0.0.0"
    port: int = 8686
    config_dir: Path = Path("/config")
    database_url: str | None = None
    log_level: str = "INFO"
    web_dir: Path = Path("/app/web/dist")
    secure_cookies: bool = False
    session_days: int = 30
    # Name of the built-in node when running with role "all".
    node_name: str | None = None

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.config_dir / 'frameforge.db'}"

    @property
    def sync_db_url(self) -> str:
        """URL for Alembic (synchronous driver)."""
        url = self.db_url
        return url.replace("sqlite+aiosqlite", "sqlite").replace("postgresql+asyncpg", "postgresql+psycopg")

    @property
    def logs_dir(self) -> Path:
        return self.config_dir / "logs"

    @property
    def job_logs_dir(self) -> Path:
        return self.logs_dir / "jobs"

    @property
    def node_logs_dir(self) -> Path:
        return self.logs_dir / "nodes"

    @property
    def previews_dir(self) -> Path:
        """Compression preview images: a disposable cache, wiped at startup."""
        return self.config_dir / "previews"

    def ensure_dirs(self) -> None:
        for d in (self.config_dir, self.logs_dir, self.job_logs_dir, self.node_logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if os.name == "nt" and str(settings.config_dir) == "\\config":  # local dev on Windows
        settings.config_dir = Path("./.dev-config").resolve()
    return settings
