"""Translate between server and node paths (shared storage mounted at different locations)."""

from __future__ import annotations

import posixpath

from .protocol import PathMapping


def _norm(path: str) -> str:
    path = path.replace("\\", "/")
    return path.rstrip("/") or "/"


def _translate(path: str, pairs: list[tuple[str, str]]) -> str:
    """Replace the longest matching ``from`` prefix (whole path segments) with its ``to``."""
    p = _norm(path)
    best: tuple[str, str] | None = None
    for src, dst in pairs:
        prefix = _norm(src)
        if (p == prefix or p.startswith(prefix + "/")) and (best is None or len(prefix) > len(_norm(best[0]))):
            best = (src, dst)
    if best is None:
        return path
    rest = p[len(_norm(best[0])) :].lstrip("/")
    return posixpath.join(_norm(best[1]), rest) if rest else _norm(best[1])


def map_path(path: str, mappings: list[PathMapping]) -> str:
    """Server path → node path. Applies the longest matching server prefix; unmapped paths are returned unchanged."""
    return _translate(path, [(m.server, m.node) for m in mappings])


def unmap_path(path: str, mappings: list[PathMapping]) -> str:
    """Node path → server path, for messages shown to users. Unmapped paths are returned unchanged."""
    return _translate(path, [(m.node, m.server) for m in mappings])
