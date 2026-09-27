"""Server → node path translation (pathmap.py)."""

from __future__ import annotations

from frameforge_shared.pathmap import map_path, unmap_path
from frameforge_shared.protocol import PathMapping

MAPS = [PathMapping(server="/media", node="/mnt/nas"), PathMapping(server="/media/vods", node="/data/vods/")]


def test_longest_prefix_wins() -> None:
    assert map_path("/media/vods/2024/a.mkv", MAPS) == "/data/vods/2024/a.mkv"
    assert map_path("/media/other/a.mkv", MAPS) == "/mnt/nas/other/a.mkv"


def test_prefix_must_match_whole_path_segments() -> None:
    assert map_path("/media/vodsextra/a.mkv", MAPS) == "/mnt/nas/vodsextra/a.mkv"
    assert map_path("/mediastuff/a.mkv", MAPS) == "/mediastuff/a.mkv"


def test_exact_root_and_unmapped() -> None:
    assert map_path("/media/vods", MAPS) == "/data/vods"
    assert map_path("/srv/a.mkv", MAPS) == "/srv/a.mkv"
    assert map_path("/srv/a.mkv", []) == "/srv/a.mkv"


def test_hidden_temp_paths_map_too() -> None:
    assert map_path("/media/vods/.frameforge-tmp/job-3.mkv", MAPS) == "/data/vods/.frameforge-tmp/job-3.mkv"


# ---------------------------------------------------------------------------
# Node → server (for messages the server shows to users)
# ---------------------------------------------------------------------------


def test_unmap_longest_node_prefix_wins() -> None:
    assert unmap_path("/data/vods/2024/a.mkv", MAPS) == "/media/vods/2024/a.mkv"
    assert unmap_path("/mnt/nas/other/a.mkv", MAPS) == "/media/other/a.mkv"


def test_unmap_prefix_must_match_whole_path_segments() -> None:
    assert unmap_path("/mnt/nasty/a.mkv", MAPS) == "/mnt/nasty/a.mkv"
    assert unmap_path("/data/vods", MAPS) == "/media/vods"


def test_unmap_leaves_unmapped_paths_alone() -> None:
    assert unmap_path("/srv/a.mkv", MAPS) == "/srv/a.mkv"
    assert unmap_path("/mnt/vods/a.mkv", []) == "/mnt/vods/a.mkv"


def test_unmap_reverses_map() -> None:
    for path in ("/media/vods/2024/a.mkv", "/media/other/.frameforge-tmp/job-3.mkv", "/media/vods/.frameforge-originals/a (1).mkv"):
        assert unmap_path(map_path(path, MAPS), MAPS) == path


def test_runner_reports_server_paths() -> None:
    from frameforge_node.runner import JobRunner

    async def send(*_: object) -> None: ...

    runner = JobRunner(send, send)  # type: ignore[arg-type]
    runner.path_mappings = [PathMapping(server="/media/test-vods", node="/mnt/vods")]
    assert runner._server_path("/mnt/vods/.frameforge-originals/stream.mkv") == "/media/test-vods/.frameforge-originals/stream.mkv"
    assert runner._server_path("/elsewhere/a.mkv") == "/elsewhere/a.mkv"
