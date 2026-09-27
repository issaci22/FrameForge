"""Server-side finalize plans (jobs.build_plan): where outputs, temp files and backups go."""

from __future__ import annotations

import posixpath

import pytest

from frameforge_server.db.models import Job, Library
from frameforge_server.services.jobs import DEFAULT_BACKUP_DIR_NAME, TMP_DIR_NAME, build_plan, frameforge_tag
from frameforge_shared.profile import ProfileSpec

MKV = ProfileSpec(container="mkv")
MP4 = ProfileSpec(container="mp4")


def job(path: str, jid: int = 42, profile_id: int = 3) -> Job:
    return Job(id=jid, source_path=path, profile_id=profile_id, profile_name="p")


def library(policy: str = "backup", **kw: str) -> Library:
    return Library(name="VODs", paths=["/media/vods"], output_policy=policy, **kw)


@pytest.mark.parametrize("policy", ["backup", "replace", "alongside"])
def test_temp_output_is_always_in_a_hidden_dir_next_to_the_final_output(policy: str) -> None:
    plan = build_plan(job("/media/vods/2024/stream.mp4"), library(policy), MKV)
    assert posixpath.dirname(plan.temp_output) == posixpath.join(posixpath.dirname(plan.final_output), TMP_DIR_NAME)
    assert plan.temp_output != plan.final_output
    assert posixpath.basename(plan.temp_output) == "job-42.mkv"


def test_output_dir_policy_mirrors_subfolders_and_keeps_original() -> None:
    plan = build_plan(job("/media/vods/2024/08/stream.mp4"), library("output_dir", output_path="/media/compressed"), MKV)
    assert plan.final_output == "/media/compressed/2024/08/stream.mkv"
    assert plan.temp_output == "/media/compressed/2024/08/.frameforge-tmp/job-42.mkv"
    assert plan.original_action == "keep"


def test_output_dir_policy_at_library_root() -> None:
    plan = build_plan(job("/media/vods/stream.mp4"), library("output_dir", output_path="/media/compressed"), MKV)
    assert plan.final_output == "/media/compressed/stream.mkv"


def test_alongside_policy_never_collides_with_source() -> None:
    plan = build_plan(job("/media/vods/stream.mkv"), library("alongside"), MKV)
    assert plan.final_output == "/media/vods/stream.ff.mkv"
    assert plan.original_action == "keep"


def test_replace_policy_deletes_after_success() -> None:
    plan = build_plan(job("/media/vods/stream.mp4"), library("replace"), MKV)
    assert plan.final_output == "/media/vods/stream.mkv"
    assert plan.original_action == "delete"
    assert plan.backup_path is None


def test_backup_policy_defaults_to_hidden_originals_dir_under_the_library_root() -> None:
    plan = build_plan(job("/media/vods/2024/stream.mkv"), library("backup"), MP4)
    assert plan.final_output == "/media/vods/2024/stream.mp4"
    assert plan.original_action == "backup"
    assert plan.backup_path == f"/media/vods/{DEFAULT_BACKUP_DIR_NAME}/2024/stream.mkv"


def test_backup_policy_with_custom_backup_folder() -> None:
    plan = build_plan(job("/media/vods/2024/stream.mkv"), library("backup", backup_path="/media/originals"), MKV)
    assert plan.backup_path == "/media/originals/2024/stream.mkv"
    assert plan.final_output == plan.source  # in-place: the finalizer parks the original first


def test_frameforge_tag_format() -> None:
    # decide() and the scanner rely on "profile=<id>;" appearing in this tag.
    assert frameforge_tag(job("/x.mkv", jid=9, profile_id=4)) == "job=9;profile=4;v=1"


# ---------------------------------------------------------------------------
# Split storage policy: where the output goes × what happens to the original
# ---------------------------------------------------------------------------

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

from frameforge_server.api.libraries import LibraryIn  # noqa: E402
from frameforge_server.services.storage_policy import StoragePolicy, apply_to, from_legacy, original_stays_in_library, storage_of  # noqa: E402
from frameforge_shared.protocol import FinalizePlan  # noqa: E402


def legacy_build_plan(job: Job, library: Library, spec: ProfileSpec) -> FinalizePlan:
    """Frozen copy of build_plan before the storage split. Legacy libraries must plan exactly like this."""
    from frameforge_server.services.jobs import CONTAINER_EXTENSIONS, Container, _library_root_for

    src = job.source_path
    src_dir, name = posixpath.split(src)
    stem, _ = posixpath.splitext(name)
    ext = CONTAINER_EXTENSIONS[Container(spec.container)]
    root = _library_root_for(library, src) or src_dir
    rel_dir = posixpath.relpath(src_dir, root) if src_dir != root else ""
    policy = library.output_policy
    if policy == "output_dir" and library.output_path:
        final = posixpath.join(library.output_path, rel_dir, stem + ext) if rel_dir else posixpath.join(library.output_path, stem + ext)
        action, backup = "keep", None
    elif policy == "alongside":
        final = posixpath.join(src_dir, f"{stem}.ff{ext}")
        action, backup = "keep", None
    else:
        final = posixpath.join(src_dir, stem + ext)
        if policy == "replace":
            action, backup = "delete", None
        else:
            backup_root = library.backup_path or posixpath.join(root, DEFAULT_BACKUP_DIR_NAME)
            backup = posixpath.join(backup_root, rel_dir, name) if rel_dir else posixpath.join(backup_root, name)
            action = "backup"
    temp = posixpath.join(posixpath.dirname(final), TMP_DIR_NAME, f"job-{job.id}{ext}")
    return FinalizePlan(source=src, temp_output=temp, final_output=final, original_action=action, backup_path=backup)


LEGACY_CASES = [
    ("backup", {}),
    ("backup", {"backup_path": "/media/originals"}),
    ("replace", {}),
    ("alongside", {}),
    ("output_dir", {"output_path": "/media/compressed"}),
    ("output_dir", {}),  # no folder: the old build_plan fell back to backup
    ("something-else", {}),
]


@pytest.mark.parametrize(("policy", "kw"), LEGACY_CASES)
@pytest.mark.parametrize("src", ["/media/vods/stream.mkv", "/media/vods/2024/08/stream.mp4"])
@pytest.mark.parametrize("spec", [MKV, MP4])
def test_split_policy_plans_exactly_like_the_old_value(policy: str, kw: dict, src: str, spec: ProfileSpec) -> None:
    old = legacy_build_plan(job(src), library(policy, **kw), spec)
    migrated = library(policy, **kw)
    apply_to(migrated, from_legacy(policy, kw.get("output_path")))
    assert build_plan(job(src), migrated, spec) == old


def lib_with(policy: StoragePolicy, **kw: str) -> Library:
    lib = Library(name="VODs", paths=["/media/vods"], **kw)
    apply_to(lib, policy)
    return lib


def test_separate_folder_can_delete_the_original_after_success() -> None:
    plan = build_plan(job("/media/vods/2024/stream.mp4"), lib_with(StoragePolicy("folder", "delete"), output_path="/media/out"), MKV)
    assert plan.final_output == "/media/out/2024/stream.mkv"
    assert plan.original_action == "delete"


@pytest.mark.parametrize(
    ("policy", "kw", "final", "action"),
    [
        (StoragePolicy("source_folder", "keep_days", 7, "backup"), {}, "/media/vods/stream.mkv", "backup"),
        (StoragePolicy("source_folder", "keep_days", 7, "in_place"), {}, "/media/vods/stream.ff.mkv", "keep"),
        (StoragePolicy("folder", "keep_days", 7), {"output_path": "/media/out"}, "/media/out/stream.mkv", "keep"),
    ],
)
def test_keep_for_a_period_never_deletes_during_finalize(policy: StoragePolicy, kw: dict, final: str, action: str) -> None:
    plan = build_plan(job("/media/vods/stream.mp4"), lib_with(policy, **kw), MKV)
    assert plan.final_output == final
    assert plan.original_action == action  # deletion happens later, in services/retention.py, after re-checking


def test_legacy_value_never_deletes_more_than_the_split_policy() -> None:
    assert StoragePolicy("source_folder", "keep_days", 7, "backup").legacy == "backup"
    assert StoragePolicy("source_folder", "keep_days", 7, "in_place").legacy == "alongside"
    assert StoragePolicy("folder", "delete").legacy == "output_dir"  # an older version would keep the original
    assert StoragePolicy("source_folder", "delete").legacy == "replace"


def test_keep_days_without_a_period_is_treated_as_keep() -> None:
    lib = Library(name="x", paths=["/m"], output_location="source_folder", original_handling="keep_days", retention_days=None, kept_original_location="backup")
    assert storage_of(lib).original_handling == "keep"


def test_which_kept_originals_stay_in_the_library() -> None:
    assert original_stays_in_library(StoragePolicy("source_folder", "keep", None, "in_place"))
    assert original_stays_in_library(StoragePolicy("source_folder", "keep_days", 3, "in_place"))
    assert original_stays_in_library(StoragePolicy("folder", "keep_days", 3))
    assert not original_stays_in_library(StoragePolicy("folder", "keep"))  # several outputs per source stay possible
    assert not original_stays_in_library(StoragePolicy("source_folder", "keep", None, "backup"))  # moved out of the library
    assert not original_stays_in_library(StoragePolicy("source_folder", "delete"))


def _migration_module():  # noqa: ANN202
    path = Path(__file__).parents[1] / "server/frameforge_server/db/migrations/versions/0003_storage_split_retention.py"
    spec = importlib.util.spec_from_file_location("m0003", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(("policy", "kw"), LEGACY_CASES)
def test_migration_mapping_matches_the_runtime_mapping(policy: str, kw: dict) -> None:
    runtime = from_legacy(policy, kw.get("output_path"))
    assert _migration_module()._from_legacy(policy, kw.get("output_path")) == (runtime.output_location, runtime.original_handling, runtime.kept_original_location)


# ---------------------------------------------------------------------------
# API input: old clients send only output_policy, new ones send the split fields
# ---------------------------------------------------------------------------


def test_old_clients_are_translated_from_output_policy() -> None:
    body = LibraryIn(name="x", paths=["/media/vods"], output_policy="alongside")
    assert body.storage() == StoragePolicy("source_folder", "keep", None, "in_place")


def test_new_fields_win_over_output_policy() -> None:
    body = LibraryIn(name="x", paths=["/media/vods"], output_policy="replace", original_handling="keep_days", retention_days=14)
    assert body.storage() == StoragePolicy("source_folder", "keep_days", 14, "backup")


def test_retention_days_are_bounded() -> None:
    with pytest.raises(ValueError):
        LibraryIn(name="x", paths=["/media/vods"], original_handling="keep_days", retention_days=0)
