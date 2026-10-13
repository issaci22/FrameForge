"""Excluded folders: parts of a library that are never scanned or converted.

Entries are absolute container paths strictly inside one of the library's folders. Matching ignores case and
works on whole path components, so excluding ``Archive`` never excludes ``Archive2``. Erring towards excluding
is the safe direction: an excluded file is only ever left alone.

Enforced in four places: the directory walk (excluded folders are never entered), the rule engine (status
``excluded``), manual queueing and retries, and the scheduler right before it starts a job.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ACTIVE_JOB_STATES, Job, Library, MediaFile
from ..events import bus
from . import queue as queue_service

log = logging.getLogger(__name__)

EXCLUDED_STATUS = "excluded"
EXCLUDED_DECISION = "In an excluded folder"
EXCLUSION_CANCEL_MESSAGE = "Cancelled: its folder is excluded in the library settings. The file wasn't touched."


def _clean(raw: str) -> str:
    p = raw.strip().replace("\\", "/")
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise ValueError(f"“{raw.strip()}” contains control characters")
    if p.startswith("/"):
        p = "/" + p.lstrip("/")  # normpath keeps a leading "//"
    return posixpath.normpath(p) if p else p


def _within(path_key: str, folder_key: str) -> bool:
    """Case-folded ``path`` is ``folder`` or inside it (whole components only)."""
    return folder_key == "/" or path_key == folder_key or path_key.startswith(folder_key + "/")


def normalize_folder(raw: str, roots: Sequence[str]) -> str:
    """Validate one excluded folder against the library's folders. Raises ValueError with a friendly message.

    A relative entry is taken relative to the library folder when there is exactly one.
    """
    path = _clean(raw)
    if not path or path == ".":
        raise ValueError("An excluded folder can't be empty")
    clean_roots = [_clean(r) for r in roots if r and r.strip()]
    if not path.startswith("/"):
        if len(clean_roots) != 1:
            raise ValueError(f"Use the full path for “{raw.strip()}”: this library has several folders")
        path = posixpath.normpath(posixpath.join(clean_roots[0], path))
    key = path.casefold()
    if any(key == r.casefold() for r in clean_roots):
        raise ValueError(f"{path} is a library folder itself. Remove it from Folders (or disable the library) instead of excluding it")
    if not any(_within(key, r.casefold()) for r in clean_roots):
        raise ValueError(f"{path} isn't inside this library's folders ({', '.join(clean_roots)})")
    return path


def normalize_folders(raws: Iterable[str], roots: Sequence[str]) -> list[str]:
    """Validate a list of excluded folders, dropping blanks and duplicates (ignoring case). Keeps the user's order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in raws:
        if not raw or not raw.strip():
            continue
        path = normalize_folder(raw, roots)
        if path.casefold() not in seen:
            seen.add(path.casefold())
            out.append(path)
    return out


class ExclusionMatcher:
    """Answers "is this path excluded?" for one library's excluded folders."""

    def __init__(self, folders: Iterable[str] | None) -> None:
        self.folders = [f for f in (_clean(x) for x in (folders or []) if x and x.strip()) if f.startswith("/")]
        self._keys = [(f.casefold(), f) for f in self.folders]
        self._real_keys: list[tuple[str, str]] | None = None

    def __bool__(self) -> bool:
        return bool(self.folders)

    def match(self, path: str) -> str | None:
        """The excluded folder that contains ``path``, judged by the path as written."""
        if not self._keys:
            return None
        key = _clean(path).casefold()
        for folder_key, folder in self._keys:
            if _within(key, folder_key):
                return folder
        return None

    def match_link(self, path: str) -> str | None:
        """For a symlink, the excluded folder its target lives in. Touches the filesystem (lstat/realpath)."""
        if not self._keys or not os.path.islink(path):
            return None
        if self._real_keys is None:
            self._real_keys = [(posixpath.normpath(os.path.realpath(f)).casefold(), f) for f in self.folders]
        key = posixpath.normpath(os.path.realpath(path)).casefold()
        for folder_key, folder in self._real_keys:
            if _within(key, folder_key):
                return folder
        return None

    def match_any(self, path: str) -> str | None:
        """By path, or (for a symlink) by where it points. Touches the filesystem."""
        return self.match(path) or self.match_link(path)


def matcher_for(library: Library | None) -> ExclusionMatcher:
    return ExclusionMatcher(library.exclude_folders if library is not None else None)


# ---------------------------------------------------------------------------
# Applying a changed exclusion list right away
# ---------------------------------------------------------------------------


async def apply(db: AsyncSession, library: Library) -> dict[str, Any]:
    """Bring queued jobs and file rows in line with the library's excluded folders. The caller commits.

    Queued jobs inside an exclusion are cancelled (without a user stop request, so removing the exclusion later
    lets rules queue them again). Running jobs keep running and are only counted. Files are marked ``excluded``,
    or restored when no exclusion covers them any more. Nothing on disk is touched.
    """
    matcher = matcher_for(library)
    jobs = (await db.execute(select(Job.id, Job.source_path, Job.state).where(Job.library_id == library.id, Job.state.in_(("queued", *ACTIVE_JOB_STATES))))).all()

    def classify_jobs() -> tuple[list[int], int]:
        queued, running = [], 0
        for job_id, path, state in jobs:
            if matcher.match_any(path):
                if state == "queued":
                    queued.append(job_id)
                else:
                    running += 1
        return queued, running

    queued_ids, running = await asyncio.to_thread(classify_jobs) if matcher else ([], 0)
    cancelled = await queue_service.cancel_queued(db, job_ids=queued_ids, message=EXCLUSION_CANCEL_MESSAGE, by_user=False, file_status=EXCLUDED_STATUS)

    # populate_existing: rows already in this session may predate the bulk update above.
    files = (await db.execute(select(MediaFile).where(MediaFile.library_id == library.id).execution_options(populate_existing=True))).scalars().all()
    rows = [(f.id, f.path, f.status) for f in files]

    def classify_files() -> set[int]:
        return {fid for fid, path, _status in rows if matcher.match_any(path)} if matcher else set()

    excluded_ids = await asyncio.to_thread(classify_files)
    newly = restored = 0
    for f in files:
        if f.id in excluded_ids:
            if f.status not in (EXCLUDED_STATUS, "queued", "processing"):
                f.status = EXCLUDED_STATUS
                newly += 1
            if f.status == EXCLUDED_STATUS:
                f.decision = EXCLUDED_DECISION
        elif f.status == EXCLUDED_STATUS:
            f.status = "ready" if f.meta else "new"
            f.decision = None
            restored += 1

    summary = {"queued_cancelled": len(cancelled), "running": running, "files_excluded": newly + len(cancelled), "files_restored": restored}
    log.info("Applied excluded folders of library %s: %s", library.id, summary)
    return summary


def publish_applied(library_id: int, summary: dict[str, Any]) -> None:
    bus.publish("queue.changed", {"library_id": library_id, **summary})
