"""Scanner safety: never pick up FrameForge's own temp files, backups or outputs (AGENTS.md §3)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from frameforge_server.db.models import Library
from frameforge_server.services.scanner import walk_library

# The server walks POSIX paths (it runs in the Linux container); run these there.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="server paths are POSIX")


def touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def scan(root: Path, **kw: object) -> set[str]:
    lib = Library(name="VODs", paths=[root.as_posix()], exclude_patterns=kw.pop("exclude_patterns", []), **kw)
    files, errors = walk_library(lib)
    assert errors == []
    return {f.relative.replace("\\", "/") for f in files}


def test_skips_temp_backup_and_hidden_dirs(tmp_path: Path) -> None:
    for rel in [
        "stream.mkv",
        "2024/old.mp4",
        ".frameforge-tmp/job-1.mkv",
        "2024/.frameforge-tmp/job-2.mkv",
        ".frameforge-originals/stream.mkv",
        ".hidden/x.mkv",
        "2024/.stream.mkv.partial.mkv",
        "notes.txt",
        "thumb.jpg",
    ]:
        touch(tmp_path, rel)
    assert scan(tmp_path) == {"stream.mkv", "2024/old.mp4"}


def test_exclude_patterns(tmp_path: Path) -> None:
    for rel in ["keep.mkv", "raw/replay-1.mkv", "clips/short.mp4"]:
        touch(tmp_path, rel)
    assert scan(tmp_path, exclude_patterns=["replay-*", "clips/*"]) == {"keep.mkv"}


def test_skips_configured_output_and_backup_folders_inside_the_library(tmp_path: Path) -> None:
    for rel in ["stream.mkv", "compressed/stream.mkv", "archive/stream.mkv"]:
        touch(tmp_path, rel)
    root = tmp_path.as_posix()
    assert scan(tmp_path, output_path=f"{root}/compressed", backup_path=f"{root}/archive/") == {"stream.mkv"}


def test_missing_root_is_reported(tmp_path: Path) -> None:
    lib = Library(name="VODs", paths=[(tmp_path / "unmounted").as_posix()], exclude_patterns=[])
    files, errors = walk_library(lib)
    assert files == [] and "not found or not mounted" in errors[0]
