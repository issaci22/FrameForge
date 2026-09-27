"""Logging: console + rotating server.log under /config/logs."""

from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUPS = 5


def configure_logging(logs_dir: Path, level: str = "INFO", filename: str = "server.log") -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = logging.Formatter(LOG_FORMAT)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    file_handler = logging.handlers.RotatingFileHandler(logs_dir / filename, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    for noisy in ("uvicorn.access", "aiosqlite", "websockets", "httpx", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def tail_file(path: Path, lines: int = 500) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return [ln.rstrip("\n") for ln in deque(fh, maxlen=lines)]
