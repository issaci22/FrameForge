"""Locating external binaries (ffmpeg, ffprobe, vainfo, nvidia-smi)."""

from __future__ import annotations

import os
import shutil
from functools import lru_cache

# jellyfin-ffmpeg installs outside PATH and bundles its own HW drivers, so prefer it.
_PREFERRED_DIR = "/usr/lib/jellyfin-ffmpeg"


@lru_cache(maxsize=None)
def find_tool(name: str) -> str | None:
    """Return an absolute path for ``name``, honouring ``FF_<NAME>_PATH`` overrides."""
    override = os.environ.get(f"FF_{name.upper().replace('-', '_')}_PATH")
    if override:
        return override
    candidate = os.path.join(_PREFERRED_DIR, name)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which(name)


def ffmpeg_path() -> str:
    return find_tool("ffmpeg") or "ffmpeg"


def ffprobe_path() -> str:
    return find_tool("ffprobe") or "ffprobe"
