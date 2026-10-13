"""Scanner safety: never pick up FrameForge's own temp files, backups or outputs (AGENTS.md §3)."""

from __future__ import annotations

import os
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
    lib = Library(name="VODs", paths=[root.as_posix()], exclude_patterns=kw.pop("exclude_patterns", []), exclude_folders=kw.pop("exclude_folders", []), **kw)
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


# ---------------------------------------------------------------------------
# Excluded folders
# ---------------------------------------------------------------------------

LAYOUT = [
    "keep.mkv",
    "Archive/old.mkv",
    "Archive/2023/older.mp4",
    "Archive2/keep-too.mkv",
    "2024/final.mkv",
    "2024/raw/take-1.mkv",
    "2024/rawer/keep.mkv",
    "Old Streams [2023] & B-roll/ep 1.mkv",
]


def test_excluded_folders_and_everything_inside_are_skipped(tmp_path: Path) -> None:
    for rel in LAYOUT:
        touch(tmp_path, rel)
    root = tmp_path.as_posix()
    excluded = [f"{root}/Archive", f"{root}/2024/raw", f"{root}/Old Streams [2023] & B-roll"]
    assert scan(tmp_path, exclude_folders=excluded) == {"keep.mkv", "Archive2/keep-too.mkv", "2024/final.mkv", "2024/rawer/keep.mkv"}


def test_excluded_folders_are_never_entered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for rel in LAYOUT:
        touch(tmp_path, rel)
    root = tmp_path.as_posix()
    visited: list[str] = []
    real_walk = os.walk

    def spy(top: str, **kw: object):  # noqa: ANN202
        for dirpath, dirnames, filenames in real_walk(top, **kw):  # type: ignore[arg-type]
            visited.append(dirpath)
            yield dirpath, dirnames, filenames

    monkeypatch.setattr("frameforge_server.services.scanner.os.walk", spy)
    scan(tmp_path, exclude_folders=[f"{root}/Archive"])
    assert not any(v == f"{root}/Archive" or v.startswith(f"{root}/Archive/") for v in visited)


def test_exclusions_ignore_case(tmp_path: Path) -> None:
    for rel in ["Archive/old.mkv", "keep.mkv"]:
        touch(tmp_path, rel)
    assert scan(tmp_path, exclude_folders=[f"{tmp_path.as_posix()}/ARCHIVE/"]) == {"keep.mkv"}


def test_symlinked_file_pointing_into_an_excluded_folder_is_skipped(tmp_path: Path) -> None:
    touch(tmp_path, "Archive/secret.mkv")
    touch(tmp_path, "keep.mkv")
    os.symlink(tmp_path / "Archive" / "secret.mkv", tmp_path / "link-to-secret.mkv")
    os.symlink(tmp_path / "keep.mkv", tmp_path / "link-to-keep.mkv")
    assert scan(tmp_path, exclude_folders=[f"{tmp_path.as_posix()}/Archive"]) == {"keep.mkv", "link-to-keep.mkv"}


def test_symlinked_folders_are_still_not_followed(tmp_path: Path) -> None:
    touch(tmp_path, "Archive/secret.mkv")
    os.symlink(tmp_path / "Archive", tmp_path / "shortcut", target_is_directory=True)
    assert scan(tmp_path, exclude_folders=[f"{tmp_path.as_posix()}/Archive"]) == set()


def test_a_library_folder_inside_another_folders_exclusion_is_skipped(tmp_path: Path) -> None:
    touch(tmp_path, "Archive/keep/old.mkv")
    touch(tmp_path, "new.mkv")
    root = tmp_path.as_posix()
    lib = Library(name="VODs", paths=[root, f"{root}/Archive/keep"], exclude_patterns=[], exclude_folders=[f"{root}/Archive"])
    files, errors = walk_library(lib)
    assert errors == [] and {f.relative for f in files} == {"new.mkv"}
