"""Excluded folders: validating entries and matching paths (pure string logic, runs on any OS)."""

from __future__ import annotations

import pytest

from frameforge_server.services.exclusions import ExclusionMatcher, normalize_folder, normalize_folders

ROOT = "/media/vods"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/media/vods/Archive", "/media/vods/Archive"),
        ("/media/vods/Archive/", "/media/vods/Archive"),  # trailing separator
        ("  /media/vods/Archive  ", "/media/vods/Archive"),
        ("\\media\\vods\\Archive\\raw", "/media/vods/Archive/raw"),  # Windows separators
        ("//media//vods///Archive", "/media/vods/Archive"),
        ("/media/vods/./Archive/../Archive", "/media/vods/Archive"),
        ("/media/vods/Old Streams [2023] & B-roll #1", "/media/vods/Old Streams [2023] & B-roll #1"),
        ("/media/vods/Ünïcødé 🎮", "/media/vods/Ünïcødé 🎮"),
        ("Archive", "/media/vods/Archive"),  # relative to the only library folder
        ("Archive\\raw", "/media/vods/Archive/raw"),
    ],
)
def test_normalize_accepts_folders_inside_the_library(raw: str, expected: str) -> None:
    assert normalize_folder(raw, [ROOT]) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("/media/vods", "is a library folder itself"),
        ("/media/vods/", "is a library folder itself"),
        ("/MEDIA/VODS", "is a library folder itself"),
        ("/media/other", "isn't inside"),
        ("/media/vods2/Archive", "isn't inside"),  # a sibling that only shares a prefix
        ("/media/vods/../other", "isn't inside"),  # traversal out of the library
        ("../other", "isn't inside"),
        ("/media/vods/Arch\x00ive", "control characters"),
        ("/media/vods/Arch\nive", "control characters"),
        (".", "can't be empty"),
    ],
)
def test_normalize_rejects_unsafe_entries(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_folder(raw, [ROOT])


def test_relative_entries_need_a_single_library_folder() -> None:
    with pytest.raises(ValueError, match="full path"):
        normalize_folder("Archive", [ROOT, "/media/clips"])
    assert normalize_folder("/media/clips/old", [ROOT, "/media/clips"]) == "/media/clips/old"


def test_a_library_rooted_at_slash_accepts_any_absolute_folder() -> None:
    assert normalize_folder("/media/x", ["/"]) == "/media/x"


def test_normalize_folders_drops_blanks_and_duplicates_ignoring_case() -> None:
    raws = ["/media/vods/Archive", "", "  ", "/media/vods/archive/", "Archive", "/media/vods/Raw"]
    assert normalize_folders(raws, [ROOT]) == ["/media/vods/Archive", "/media/vods/Raw"]


def test_normalize_folders_reports_the_bad_entry() -> None:
    with pytest.raises(ValueError, match="/media/elsewhere"):
        normalize_folders(["/media/vods/ok", "/media/elsewhere"], [ROOT])


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def test_matches_the_folder_and_everything_below_it() -> None:
    m = ExclusionMatcher(["/media/vods/Archive"])
    assert m.match("/media/vods/Archive") == "/media/vods/Archive"
    assert m.match("/media/vods/Archive/a.mkv") == "/media/vods/Archive"
    assert m.match("/media/vods/Archive/2023/deep/b.mp4") == "/media/vods/Archive"


@pytest.mark.parametrize("path", ["/media/vods/Archive2/a.mkv", "/media/vods/Archive.old/a.mkv", "/media/vods/MyArchive/a.mkv", "/media/vods/a.mkv", "/media/vods/Archive2"])
def test_similar_names_are_not_excluded(path: str) -> None:
    assert ExclusionMatcher(["/media/vods/Archive"]).match(path) is None


@pytest.mark.parametrize("path", ["/media/vods/archive/a.mkv", "/MEDIA/VODS/ARCHIVE/A.MKV", "\\media\\vods\\Archive\\a.mkv", "/media/vods//Archive/./a.mkv"])
def test_matching_ignores_case_and_separator_style(path: str) -> None:
    assert ExclusionMatcher(["/media/vods/Archive/"]).match(path) == "/media/vods/Archive"


def test_nested_exclusions_at_different_depths() -> None:
    m = ExclusionMatcher(["/media/vods/2024/raw", "/media/vods/Clips"])
    assert m.match("/media/vods/2024/raw/x.mkv") == "/media/vods/2024/raw"
    assert m.match("/media/vods/2024/final.mkv") is None
    assert m.match("/media/vods/2024/rawer/x.mkv") is None
    assert m.match("/media/vods/Clips/short.mp4") == "/media/vods/Clips"


def test_paths_with_spaces_and_special_characters() -> None:
    folder = "/media/vods/Old Streams [2023] & B-roll"
    m = ExclusionMatcher([folder])
    assert m.match(f"{folder}/ep 1 (final).mkv") == folder
    assert m.match("/media/vods/Old Streams [2023]/x.mkv") is None


def test_empty_or_malformed_entries_exclude_nothing() -> None:
    assert not ExclusionMatcher([])
    assert not ExclusionMatcher(None)
    m = ExclusionMatcher(["", "  ", "relative/entry"])  # never stored by the API, but must not match everything
    assert not m
    assert m.match("/media/vods/relative/entry/a.mkv") is None
