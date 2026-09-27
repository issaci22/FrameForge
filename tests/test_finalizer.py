"""Finalizer safety (AGENTS.md §3): placement, original handling, rollback and crash recovery.

Files are plain bytes: sources contain ``SOURCE``, transcoded outputs contain ``OUTPUT``. The verifier
accepts only outputs, mirroring the real one (which requires this job's FRAMEFORGE tag), so an untouched
original can never pass as a finished output.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from frameforge_node import finalizer
from frameforge_node.finalizer import FinalizeError, finalize, hold_path, recover
from frameforge_shared.protocol import FinalizePlan

SOURCE = b"SOURCE" * 1000
OUTPUT = b"OUTPUT" * 500
ORIGINAL_MTIME = 1_600_000_000  # 2020-09-13, an "old" recording


class Crash(Exception):
    """Simulated process death at a journal point."""


@dataclass
class Steps:
    """on_step callback that records messages + journal steps, and can crash at a given step."""

    plan: FinalizePlan | None = None
    crash_at: str | None = None
    messages: list[str] = field(default_factory=list)
    journal: list[str] = field(default_factory=list)

    async def __call__(self, msg: str) -> None:
        self.messages.append(msg)
        step = read_journal(self.plan) if self.plan else None
        if step and (not self.journal or self.journal[-1] != step):
            self.journal.append(step)
        if self.crash_at is not None and step == self.crash_at:
            raise Crash(step)


def read_journal(plan: FinalizePlan) -> str | None:
    try:
        with open(plan.temp_output + ".journal", encoding="utf-8") as fh:
            return json.load(fh)["step"]
    except OSError:
        return None


async def verify_output(path: str) -> bool:
    return Path(path).read_bytes().startswith(b"OUTPUT")


async def verify_nothing(path: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# Scenario setup
# ---------------------------------------------------------------------------


@dataclass
class Scene:
    root: Path
    plan: FinalizePlan

    @property
    def source(self) -> Path:
        return Path(self.plan.source)

    @property
    def final(self) -> Path:
        return Path(self.plan.final_output)

    @property
    def temp(self) -> Path:
        return Path(self.plan.temp_output)

    @property
    def held(self) -> Path:
        return Path(hold_path(self.plan))

    @property
    def backup(self) -> Path | None:
        return Path(self.plan.backup_path) if self.plan.backup_path else None

    def all_bytes(self) -> list[bytes]:
        """Contents of every regular file under the root (to prove SOURCE survives somewhere)."""
        return [p.read_bytes() for p in self.root.rglob("*") if p.is_file()]


def scene(tmp_path: Path, *, same_path: bool, action: str, output_dir: bool = False) -> Scene:
    vods = tmp_path / "vods"
    vods.mkdir()
    src_ext = ".mkv" if same_path else ".mp4"
    source = vods / f"stream{src_ext}"
    source.write_bytes(SOURCE)
    os.utime(source, (ORIGINAL_MTIME, ORIGINAL_MTIME))

    final_dir = tmp_path / "compressed" if output_dir else vods
    final_dir.mkdir(exist_ok=True)
    final = final_dir / "stream.mkv"
    temp = final_dir / ".frameforge-tmp" / "job-1.mkv"
    temp.parent.mkdir()
    temp.write_bytes(OUTPUT)
    os.utime(temp, (ORIGINAL_MTIME, ORIGINAL_MTIME))  # the runner copies the original's timestamps

    backup = str(vods / ".frameforge-originals" / source.name) if action == "backup" else None
    plan = FinalizePlan(source=str(source), temp_output=str(temp), final_output=str(final), original_action=action, backup_path=backup)  # type: ignore[arg-type]
    return Scene(tmp_path, plan)


# (same_path, action, output_dir)
LAYOUTS = [
    pytest.param(True, "backup", False, id="in-place-backup"),
    pytest.param(True, "delete", False, id="in-place-replace"),
    pytest.param(False, "backup", False, id="new-ext-backup"),
    pytest.param(False, "delete", False, id="new-ext-replace"),
    pytest.param(False, "keep", True, id="output-dir-keep"),
]


def assert_success(s: Scene) -> None:
    assert s.final.read_bytes() == OUTPUT
    assert not s.temp.exists()
    assert not s.held.exists()
    assert not Path(s.plan.temp_output + ".journal").exists()
    assert not s.temp.parent.exists(), "empty .frameforge-tmp dir is removed"
    if s.plan.original_action == "backup":
        assert s.backup is not None and s.backup.read_bytes() == SOURCE
        if s.source != s.final:
            assert not s.source.exists()
    elif s.plan.original_action == "delete":
        assert SOURCE not in s.all_bytes()
    else:
        assert s.source.read_bytes() == SOURCE


def assert_source_untouched(s: Scene) -> None:
    assert s.source.read_bytes() == SOURCE
    assert s.source.stat().st_mtime == ORIGINAL_MTIME


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("same_path", "action", "output_dir"), LAYOUTS)
async def test_finalize_succeeds(tmp_path: Path, same_path: bool, action: str, output_dir: bool) -> None:
    s = scene(tmp_path, same_path=same_path, action=action, output_dir=output_dir)
    steps = Steps(s.plan)
    outcome = await finalize(s.plan, steps, verify_output)
    assert outcome.final_path == str(s.final)
    assert outcome.output_size == len(OUTPUT)
    assert outcome.warnings == []
    assert_success(s)


@pytest.mark.parametrize(("same_path", "action", "output_dir"), LAYOUTS)
async def test_output_keeps_original_timestamps(tmp_path: Path, same_path: bool, action: str, output_dir: bool) -> None:
    s = scene(tmp_path, same_path=same_path, action=action, output_dir=output_dir)
    await finalize(s.plan, Steps(s.plan), verify_output)
    assert s.final.stat().st_mtime == ORIGINAL_MTIME
    if s.backup:
        assert s.backup.stat().st_mtime == ORIGINAL_MTIME


async def test_journal_records_every_step_in_order(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    steps = Steps(s.plan)
    await finalize(s.plan, steps, verify_output)
    assert steps.journal == ["holding_original", "placing_output", "verifying", "backing_up_original"]


async def test_original_is_only_removed_after_verification(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=False, action="delete")
    order: list[str] = []

    async def verify(path: str) -> bool:
        order.append("verify")
        assert s.source.exists(), "original must still exist while the output is verified"
        return await verify_output(path)

    async def on_step(msg: str) -> None:
        if read_journal(s.plan) == "deleting_original":
            order.append("delete")

    await finalize(s.plan, on_step, verify)
    assert order == ["verify", "delete"]


async def test_backup_name_collision_never_overwrites_an_old_backup(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    assert s.backup is not None
    s.backup.parent.mkdir(parents=True)
    s.backup.write_bytes(b"OLDER BACKUP")
    await finalize(s.plan, Steps(s.plan), verify_output)
    assert s.backup.read_bytes() == b"OLDER BACKUP"
    assert (s.backup.parent / "stream (1).mkv").read_bytes() == SOURCE


async def test_cross_device_backup_copies_and_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    real = finalizer._place_no_clobber

    def no_rename_into_backup(src: str, dst: str) -> None:
        if ".frameforge-originals" in dst:
            raise OSError(18, "Invalid cross-device link")
        real(src, dst)

    monkeypatch.setattr(finalizer, "_place_no_clobber", no_rename_into_backup)
    await finalize(s.plan, Steps(s.plan), verify_output)
    assert_success(s)
    assert s.backup is not None and s.backup.stat().st_mtime == ORIGINAL_MTIME
    assert not list(s.backup.parent.glob("*.frameforge-partial"))


# ---------------------------------------------------------------------------
# Refusals: nothing may be touched
# ---------------------------------------------------------------------------


async def test_never_overwrites_a_file_it_did_not_create(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=False, action="delete")
    s.final.write_bytes(b"USER FILE")
    with pytest.raises(FinalizeError) as exc:
        await finalize(s.plan, Steps(s.plan), verify_output)
    assert exc.value.diagnosis.code == "output_conflict"
    assert s.final.read_bytes() == b"USER FILE"
    assert_source_untouched(s)


async def test_keep_with_same_path_is_refused(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="keep")
    with pytest.raises(FinalizeError) as exc:
        await finalize(s.plan, Steps(s.plan), verify_output)
    assert exc.value.diagnosis.code == "plan_conflict"
    assert_source_untouched(s)


async def test_backup_without_backup_path_is_refused(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    s.plan.backup_path = None
    with pytest.raises(FinalizeError) as exc:
        await finalize(s.plan, Steps(s.plan), verify_output)
    assert exc.value.diagnosis.code == "plan_conflict"
    assert_source_untouched(s)


async def test_missing_temp_output_is_refused(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="delete")
    s.temp.unlink()
    with pytest.raises(FinalizeError) as exc:
        await finalize(s.plan, Steps(s.plan), verify_output)
    assert exc.value.diagnosis.code == "output_missing"
    assert_source_untouched(s)


# ---------------------------------------------------------------------------
# Rollback when the final re-probe fails
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("same_path", "action", "output_dir"), LAYOUTS)
async def test_failed_final_check_rolls_back(tmp_path: Path, same_path: bool, action: str, output_dir: bool) -> None:
    s = scene(tmp_path, same_path=same_path, action=action, output_dir=output_dir)
    with pytest.raises(FinalizeError) as exc:
        await finalize(s.plan, Steps(s.plan), verify_nothing)
    assert exc.value.diagnosis.code == "verify_after_move_failed"
    assert_source_untouched(s)
    assert not s.held.exists()
    assert not s.temp.exists()
    if not same_path:
        assert not s.final.exists()
    if s.backup:
        assert not s.backup.exists()


# ---------------------------------------------------------------------------
# Failing to handle the original never loses it
# ---------------------------------------------------------------------------


def _block_backup_dir(s: Scene) -> None:
    assert s.backup is not None
    s.backup.parent.write_bytes(b"not a directory")  # makedirs() for the backup will fail


async def test_in_place_backup_failure_rescues_the_original(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    _block_backup_dir(s)
    outcome = await finalize(s.plan, Steps(s.plan), verify_output)
    rescued = s.source.with_name("stream.original.mkv")
    assert rescued.read_bytes() == SOURCE
    assert s.final.read_bytes() == OUTPUT
    assert not s.held.exists(), "the original must not be left in the hidden temp folder"
    assert any("kept as" in w for w in outcome.warnings)


async def test_backup_failure_leaves_original_in_place(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=False, action="backup")
    _block_backup_dir(s)
    outcome = await finalize(s.plan, Steps(s.plan), verify_output)
    assert_source_untouched(s)
    assert s.final.read_bytes() == OUTPUT
    assert any("still at" in w for w in outcome.warnings)


# ---------------------------------------------------------------------------
# Crash recovery: kill the finalizer at every journal step, then recover from disk
# ---------------------------------------------------------------------------

STEPS_BY_LAYOUT = {
    "in-place-backup": ["holding_original", "placing_output", "verifying", "backing_up_original"],
    "in-place-replace": ["holding_original", "placing_output", "verifying", "deleting_original"],
    "new-ext-backup": ["placing_output", "verifying", "backing_up_original"],
    "new-ext-replace": ["placing_output", "verifying", "deleting_original"],
    "output-dir-keep": ["placing_output", "verifying"],
}
CRASH_CASES = [
    pytest.param(p.values[0], p.values[1], p.values[2], step, id=f"{p.id}@{step}")
    for p in LAYOUTS
    for step in STEPS_BY_LAYOUT[p.id]  # type: ignore[index]
]


@pytest.mark.parametrize(("same_path", "action", "output_dir", "crash_at"), CRASH_CASES)
async def test_crash_then_recover_completes(tmp_path: Path, same_path: bool, action: str, output_dir: bool, crash_at: str) -> None:
    s = scene(tmp_path, same_path=same_path, action=action, output_dir=output_dir)
    with pytest.raises(Crash):
        await finalize(s.plan, Steps(s.plan, crash_at=crash_at), verify_output)
    assert SOURCE in s.all_bytes(), "the original must survive the crash"
    assert read_journal(s.plan) == crash_at

    outcome = await recover(s.plan, Steps(), verify_output)
    assert outcome.final_path == str(s.final)
    assert_success(s)


@pytest.mark.parametrize(("same_path", "action", "output_dir", "crash_at"), CRASH_CASES)
async def test_crash_then_recover_with_bad_output_restores_original(tmp_path: Path, same_path: bool, action: str, output_dir: bool, crash_at: str) -> None:
    if crash_at in ("backing_up_original", "deleting_original"):
        pytest.skip("output was already verified before this step")
    s = scene(tmp_path, same_path=same_path, action=action, output_dir=output_dir)
    with pytest.raises(Crash):
        await finalize(s.plan, Steps(s.plan, crash_at=crash_at), verify_output)
    with pytest.raises(FinalizeError):
        await recover(s.plan, Steps(), verify_nothing)
    assert_source_untouched(s)
    assert not s.held.exists()


async def test_recover_when_nothing_started_and_temp_lost(tmp_path: Path) -> None:
    # Same path, crash before the original was set aside, temp output gone: the "final" is the original.
    s = scene(tmp_path, same_path=True, action="delete")
    s.temp.unlink()
    with pytest.raises(FinalizeError) as exc:
        await recover(s.plan, Steps(), verify_output)
    assert exc.value.diagnosis.code == "recovered_untouched"
    assert_source_untouched(s)


async def test_recover_when_held_original_exists_but_output_lost(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="delete")
    with pytest.raises(Crash):
        await finalize(s.plan, Steps(s.plan, crash_at="placing_output"), verify_output)
    s.temp.unlink()  # output vanished while the node was down
    with pytest.raises(FinalizeError) as exc:
        await recover(s.plan, Steps(), verify_output)
    assert exc.value.diagnosis.code == "recovered_rollback"
    assert_source_untouched(s)


async def test_recover_with_nothing_on_disk_is_reported_not_guessed(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="delete")
    s.source.unlink()
    s.temp.unlink()
    with pytest.raises(FinalizeError) as exc:
        await recover(s.plan, Steps(), verify_output)
    assert exc.value.diagnosis.code == "recover_inconsistent"


async def test_recover_after_completed_finalize_is_idempotent(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    await finalize(s.plan, Steps(s.plan), verify_output)
    outcome = await recover(s.plan, Steps(), verify_output)
    assert outcome.final_path == str(s.final)
    assert_success(s)


async def test_recover_does_not_trust_a_foreign_file_at_the_output_path(tmp_path: Path) -> None:
    # A user file appeared at the output path while the node was down; it isn't our output.
    s = scene(tmp_path, same_path=False, action="delete")
    with pytest.raises(Crash):
        await finalize(s.plan, Steps(s.plan, crash_at="placing_output"), verify_output)
    s.final.write_bytes(b"USER FILE")
    with pytest.raises(FinalizeError) as exc:
        await recover(s.plan, Steps(), verify_output)
    assert exc.value.diagnosis.code == "output_conflict"
    assert s.final.read_bytes() == b"USER FILE"
    assert_source_untouched(s)


async def test_recover_after_crash_between_link_and_unlink(tmp_path: Path) -> None:
    # _place_no_clobber hard-links temp → final, then removes temp. Crash in between.
    s = scene(tmp_path, same_path=False, action="delete")
    os.link(s.temp, s.final)
    outcome = await recover(s.plan, Steps(), verify_output)
    assert outcome.final_path == str(s.final)
    assert_success(s)


# ---------------------------------------------------------------------------
# Messages use server paths: the node works on its own mount, users know the server's
# ---------------------------------------------------------------------------


def as_server(tmp_path: Path):  # noqa: ANN201
    """The path display the runner passes: node mount (tmp_path) → server library root (/media)."""
    return lambda p: "/media" + p[len(str(tmp_path)) :].replace(os.sep, "/") if p.startswith(str(tmp_path)) else p


def assert_no_node_paths(tmp_path: Path, texts: list[str]) -> None:
    leaked = [t for t in texts if str(tmp_path) in t]
    assert not leaked, leaked


async def test_backup_step_shows_the_server_path(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    steps = Steps(s.plan)
    await finalize(s.plan, steps, verify_output, show=as_server(tmp_path))
    assert "Moving the original to /media/vods/.frameforge-originals/stream.mkv" in steps.messages
    assert_no_node_paths(tmp_path, steps.messages)
    assert_success(s)  # the files themselves still move on the node's paths


@pytest.mark.parametrize(("same_path", "expected"), [(True, "kept as /media/vods/stream.original.mkv"), (False, "still at /media/vods/stream.mp4")])
async def test_backup_failure_warnings_show_server_paths(tmp_path: Path, same_path: bool, expected: str) -> None:
    s = scene(tmp_path, same_path=same_path, action="backup")
    _block_backup_dir(s)
    outcome = await finalize(s.plan, Steps(s.plan), verify_output, show=as_server(tmp_path))
    assert any(expected in w for w in outcome.warnings), outcome.warnings
    assert_no_node_paths(tmp_path, outcome.warnings)


async def test_output_conflict_shows_the_server_path(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=False, action="backup")
    s.final.write_bytes(b"someone else's file")
    with pytest.raises(FinalizeError) as exc:
        await finalize(s.plan, Steps(s.plan), verify_output, show=as_server(tmp_path))
    assert exc.value.diagnosis.explanation.startswith("/media/vods/stream.mkv already exists")
    assert_source_untouched(s)


async def test_unrecoverable_state_shows_server_paths(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    s.source.unlink()
    s.temp.unlink()
    with pytest.raises(FinalizeError) as exc:
        await recover(s.plan, Steps(s.plan), verify_output, show=as_server(tmp_path))
    diag = exc.value.diagnosis
    assert "/media/vods/stream.mkv" in diag.explanation and "/media/vods/.frameforge-tmp/job-1.mkv.original" in diag.explanation
    assert_no_node_paths(tmp_path, [diag.explanation])


async def test_recover_steps_show_server_paths(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    steps = Steps(s.plan, crash_at="backing_up_original")
    with pytest.raises(Crash):
        await finalize(s.plan, steps, verify_output)
    steps = Steps(s.plan)
    await recover(s.plan, steps, verify_output, show=as_server(tmp_path))
    assert "Moving the original to /media/vods/.frameforge-originals/stream.mkv" in steps.messages
    assert_no_node_paths(tmp_path, steps.messages)
    assert_success(s)


# ---------------------------------------------------------------------------
# The outcome reports where the original really ended up (retention depends on it)
# ---------------------------------------------------------------------------

EXPECTED_DISPOSITION = {
    "in-place-backup": "backed_up",
    "in-place-replace": "deleted",
    "new-ext-backup": "backed_up",
    "new-ext-replace": "deleted",
    "output-dir-keep": "kept",
}


@pytest.mark.parametrize(("same_path", "action", "output_dir"), LAYOUTS)
async def test_outcome_reports_the_original(tmp_path: Path, request: pytest.FixtureRequest, same_path: bool, action: str, output_dir: bool) -> None:
    s = scene(tmp_path, same_path=same_path, action=action, output_dir=output_dir)
    outcome = await finalize(s.plan, Steps(s.plan), verify_output)
    layout = request.node.callspec.id
    assert outcome.original_disposition == EXPECTED_DISPOSITION[layout]
    if outcome.original_disposition == "deleted":
        assert outcome.original_path is None
    else:
        assert outcome.original_path is not None
        assert Path(outcome.original_path).read_bytes() == SOURCE


async def test_outcome_reports_the_real_backup_name_after_a_collision(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    assert s.backup is not None
    s.backup.parent.mkdir(parents=True)
    s.backup.write_bytes(b"OLDER BACKUP")
    outcome = await finalize(s.plan, Steps(s.plan), verify_output)
    assert outcome.original_disposition == "backed_up"
    assert outcome.original_path == str(s.backup.parent / "stream (1).mkv")


async def test_outcome_reports_a_rescued_original(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    _block_backup_dir(s)
    outcome = await finalize(s.plan, Steps(s.plan), verify_output)
    assert outcome.original_disposition == "rescued"
    assert outcome.original_path == str(s.source.with_name("stream.original.mkv"))


async def test_outcome_reports_an_original_left_in_place_after_a_failed_backup(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=False, action="backup")
    _block_backup_dir(s)
    outcome = await finalize(s.plan, Steps(s.plan), verify_output)
    assert outcome.original_disposition == "kept"
    assert outcome.original_path == str(s.source)


async def test_journal_records_the_backup_destination(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    with pytest.raises(Crash):
        await finalize(s.plan, Steps(s.plan, crash_at="backing_up_original"), verify_output)
    with open(s.plan.temp_output + ".journal", encoding="utf-8") as fh:
        assert json.load(fh)["backup_dest"] == s.plan.backup_path


@pytest.mark.parametrize("same_path", [True, False])
async def test_recover_after_backup_reports_the_backup(tmp_path: Path, same_path: bool) -> None:
    s = scene(tmp_path, same_path=same_path, action="backup")
    with pytest.raises(Crash):
        await finalize(s.plan, Steps(s.plan, crash_at="backing_up_original"), verify_output)
    outcome = await recover(s.plan, Steps(), verify_output)
    assert outcome.original_disposition == "backed_up"
    assert outcome.original_path is not None and Path(outcome.original_path).read_bytes() == SOURCE


async def test_recover_after_the_original_was_already_moved_uses_the_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Crash after the backup move finished but before the journal was cleaned up.
    s = scene(tmp_path, same_path=False, action="backup")
    monkeypatch.setattr(finalizer, "_cleanup", lambda plan: (_ for _ in ()).throw(Crash("cleanup")))
    with pytest.raises(Crash):
        await finalize(s.plan, Steps(s.plan), verify_output)
    monkeypatch.undo()
    assert not s.source.exists()
    outcome = await recover(s.plan, Steps(), verify_output)
    assert outcome.original_disposition == "backed_up"
    assert outcome.original_path == s.plan.backup_path


async def test_recover_without_any_record_says_unknown(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=True, action="backup")
    await finalize(s.plan, Steps(s.plan), verify_output)
    outcome = await recover(s.plan, Steps(), verify_output)  # journal is gone: don't guess
    assert outcome.original_disposition == "unknown"
    assert outcome.original_path is None


# ---------------------------------------------------------------------------
# Preflight: the runner refuses impossible plans BEFORE spending hours encoding
# ---------------------------------------------------------------------------


async def test_preflight_refuses_an_existing_output_without_touching_anything(tmp_path: Path) -> None:
    s = scene(tmp_path, same_path=False, action="keep", output_dir=True)
    s.temp.unlink()  # before encoding there is no temp output yet
    s.final.write_bytes(b"EARLIER OUTPUT")
    with pytest.raises(FinalizeError) as exc:
        finalizer.preflight(s.plan)
    assert exc.value.diagnosis.code == "output_conflict"
    assert s.final.read_bytes() == b"EARLIER OUTPUT"
    assert_source_untouched(s)


@pytest.mark.parametrize(("same_path", "action"), [(True, "keep"), (True, "backup")])
async def test_preflight_refuses_bad_plans(tmp_path: Path, same_path: bool, action: str) -> None:
    s = scene(tmp_path, same_path=same_path, action=action)
    if action == "backup":
        s.plan.backup_path = None
    with pytest.raises(FinalizeError) as exc:
        finalizer.preflight(s.plan)
    assert exc.value.diagnosis.code == "plan_conflict"


@pytest.mark.parametrize(("same_path", "action", "output_dir"), LAYOUTS)
async def test_preflight_accepts_every_normal_layout(tmp_path: Path, same_path: bool, action: str, output_dir: bool) -> None:
    s = scene(tmp_path, same_path=same_path, action=action, output_dir=output_dir)
    s.temp.unlink()
    finalizer.preflight(s.plan)  # no exception
