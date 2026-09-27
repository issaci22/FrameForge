"""Timed deletion of kept originals (services/retention.py). Every check must be able to stop a deletion on its own."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from frameforge_server.db.models import Job, Library, MediaFile, RetainedOriginal
from frameforge_server.db.session import sessionmaker
from frameforge_server.db.types import utcnow
from frameforge_server.services import retention
from frameforge_server.services.jobs import record_result
from frameforge_shared.fingerprint import fingerprint_file
from frameforge_shared.protocol import FinalizePlan, JobResult

from .conftest import make_clip, needs_ffmpeg

pytestmark = needs_ffmpeg


@dataclass
class World:
    root: Path
    original: Path
    output: Path
    library_id: int
    entry_id: int


def _library(root: Path, **kw: object) -> Library:
    base: dict = {
        "name": "VODs",
        "paths": [root.as_posix()],
        "output_policy": "alongside",
        "output_location": "source_folder",
        "original_handling": "keep_days",
        "retention_days": 7,
        "kept_original_location": "in_place",
    }
    base.update(kw)
    return Library(**base)


@pytest.fixture
async def world(db_settings, tmp_path: Path) -> World:  # noqa: ANN001
    root = tmp_path / "vods"
    root.mkdir()
    original = make_clip(root / "stream.mp4", seconds=2.0)
    output = make_clip(root / "stream.ff.mkv", seconds=2.0, tag="job=7;profile=1;v=1")
    async with sessionmaker()() as db:
        lib = _library(root)
        db.add(lib)
        await db.flush()
        entry = RetainedOriginal(
            library_id=lib.id,
            job_id=7,
            output_job_id=7,
            original_path=str(original),
            allowed_root=root.as_posix(),
            original_size=original.stat().st_size,
            original_fingerprint=fingerprint_file(str(original)),
            original_duration=2.0,
            output_path=str(output),
            output_size=output.stat().st_size,
            output_fingerprint=fingerprint_file(str(output)),
            due_at=utcnow() - timedelta(minutes=1),
            state="pending",
        )
        db.add(entry)
        await db.commit()
        return World(root, original, output, lib.id, entry.id)


async def process(w: World, *, manual: bool = False, **changes: object) -> RetainedOriginal:
    async with retention.lock, sessionmaker()() as db:
        entry = await db.get(RetainedOriginal, w.entry_id)
        assert entry is not None
        for k, v in changes.items():
            setattr(entry, k, v)
        await retention.process_entry(db, entry, manual=manual)
        await db.commit()
        return entry


def assert_kept(w: World, entry: RetainedOriginal, reason_part: str) -> None:
    assert w.original.exists(), "the original must survive"
    assert entry.state == "blocked", (entry.state, entry.reason)
    assert reason_part.lower() in (entry.reason or "").lower(), entry.reason


# ---------------------------------------------------------------------------
# The normal path
# ---------------------------------------------------------------------------


async def test_due_original_is_deleted_after_every_check_passes(world: World) -> None:
    assert await retention.run_due() == 1
    assert not world.original.exists()
    assert world.output.exists()
    async with sessionmaker()() as db:
        entry = await db.get(RetainedOriginal, world.entry_id)
        assert entry is not None and entry.state == "deleted" and entry.deleted_at is not None


async def test_not_yet_due_is_left_alone(world: World) -> None:
    async with sessionmaker()() as db:
        entry = await db.get(RetainedOriginal, world.entry_id)
        assert entry is not None
        entry.due_at = utcnow() + timedelta(days=3)
        await db.commit()
    assert await retention.run_due() == 0
    assert world.original.exists()


# ---------------------------------------------------------------------------
# Each check blocks deletion on its own
# ---------------------------------------------------------------------------


async def test_missing_output_blocks(world: World) -> None:
    world.output.unlink()
    assert_kept(world, await process(world), "missing")


async def test_changed_output_blocks(world: World) -> None:
    with open(world.output, "ab") as fh:
        fh.write(b"x")
    assert_kept(world, await process(world), "converted file changed")


async def test_output_from_another_job_blocks(world: World) -> None:
    assert_kept(world, await process(world, output_job_id=8), "tag")


async def test_unreadable_output_blocks(world: World) -> None:
    world.output.write_bytes(b"not a video" * 1000)
    assert_kept(world, await process(world, output_size=None, output_fingerprint=None), "can't be read")


async def test_duration_mismatch_blocks(world: World) -> None:
    assert_kept(world, await process(world, original_duration=100.0), "duration")


async def test_changed_original_blocks(world: World) -> None:
    with open(world.original, "ab") as fh:
        fh.write(b"x")
    assert_kept(world, await process(world), "original changed")


async def test_same_size_but_different_original_blocks(world: World) -> None:
    data = bytearray(world.original.read_bytes())
    data[len(data) // 2] ^= 0xFF
    world.original.write_bytes(bytes(data))
    assert_kept(world, await process(world), "different content")


async def test_original_outside_allowed_root_blocks(world: World, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert_kept(world, await process(world, allowed_root=other.as_posix()), "outside")


async def test_symlinked_original_is_never_deleted(world: World) -> None:
    real = world.root / "real.mp4"
    os.replace(world.original, real)
    os.symlink(real, world.original)
    entry = await process(world)
    assert entry.state == "blocked" and real.exists() and os.path.islink(world.original)


async def test_active_job_on_either_file_blocks(world: World) -> None:
    async with sessionmaker()() as db:
        db.add(Job(source_path=str(world.output), profile_name="p", state="transcoding"))
        await db.commit()
    assert_kept(world, await process(world), "using this file")


async def test_output_equal_to_original_blocks(world: World) -> None:
    assert_kept(world, await process(world, output_path=str(world.original)), "same file")


# ---------------------------------------------------------------------------
# Paused, gone, manual, moved
# ---------------------------------------------------------------------------


async def test_library_no_longer_timed_pauses_deletion(world: World) -> None:
    async with sessionmaker()() as db:
        lib = await db.get(Library, world.library_id)
        assert lib is not None
        lib.original_handling = "keep"
        await db.commit()
    assert await retention.run_due() == 0
    assert world.original.exists()
    async with sessionmaker()() as db:
        entry = await db.get(RetainedOriginal, world.entry_id)
        assert entry is not None and entry.state == "pending"


async def test_deleted_library_pauses_deletion(world: World) -> None:
    async with sessionmaker()() as db:
        lib = await db.get(Library, world.library_id)
        await db.delete(lib)
        await db.commit()
    assert await retention.run_due() == 0
    assert world.original.exists()


async def test_original_already_gone_is_recorded(world: World) -> None:
    world.original.unlink()
    entry = await process(world)
    assert entry.state == "gone"


async def test_manual_delete_still_checks_everything(world: World) -> None:
    world.output.unlink()
    entry = await process(world, manual=True, due_at=utcnow() + timedelta(days=30))
    assert_kept(world, entry, "missing")


async def test_manual_delete_works_before_the_due_date(world: World) -> None:
    entry = await process(world, manual=True, due_at=utcnow() + timedelta(days=30))
    assert entry.state == "deleted" and not world.original.exists()


async def test_moved_output_is_found_by_fingerprint(world: World) -> None:
    moved = world.root / "archive" / "renamed.mkv"
    moved.parent.mkdir()
    os.replace(world.output, moved)
    async with sessionmaker()() as db:
        db.add(
            MediaFile(
                library_id=world.library_id,
                path=str(moved),
                relative_path="archive/renamed.mkv",
                filename="renamed.mkv",
                extension=".mkv",
                size=moved.stat().st_size,
                mtime=utcnow(),
                fingerprint=fingerprint_file(str(moved)),
                status="processed",
            )
        )
        await db.commit()
    entry = await process(world)
    assert entry.state == "deleted" and entry.output_path == str(moved)


# ---------------------------------------------------------------------------
# Changing the period
# ---------------------------------------------------------------------------


async def test_shorter_period_never_brings_deletion_forward(world: World) -> None:
    async with sessionmaker()() as db:
        entry = await db.get(RetainedOriginal, world.entry_id)
        lib = await db.get(Library, world.library_id)
        assert entry is not None and lib is not None
        entry.due_at = entry.created_at + timedelta(days=7)
        before = entry.due_at
        lib.retention_days = 1
        await retention.on_retention_days_changed(db, lib, old_days=7)
        assert entry.due_at == before
        lib.retention_days = 30
        await retention.on_retention_days_changed(db, lib, old_days=1)
        assert entry.due_at == entry.created_at + timedelta(days=30)
        # Only the explicit action shortens it.
        lib.retention_days = 1
        assert await retention.apply_period(db, lib) == 1
        assert entry.due_at == entry.created_at + timedelta(days=1)


# ---------------------------------------------------------------------------
# Entries come only from completed jobs (record_result)
# ---------------------------------------------------------------------------


async def _job_with_result(root: Path, status: str, **result_kw: object) -> list[RetainedOriginal]:
    async with sessionmaker()() as db:
        lib = _library(root, name=f"lib-{status}-{len(result_kw)}")
        db.add(lib)
        await db.flush()
        src = (root / "a.mp4").as_posix()
        plan = FinalizePlan(source=src, temp_output=(root / ".frameforge-tmp/job.mkv").as_posix(), final_output=(root / "a.ff.mkv").as_posix(), original_action="keep")
        job = Job(source_path=src, profile_name="p", state="finalizing", library_id=lib.id, finalize_plan=plan.model_dump(), source_size=10, source_duration=2.0)
        db.add(job)
        await db.flush()
        result = JobResult(job_id=job.id, status=status, final_path=plan.final_output if status == "completed" else None, output_size=5, **result_kw)  # type: ignore[arg-type]
        await record_result(db, job, result, max_attempts=1)
        await db.commit()
        from sqlalchemy import select

        return list((await db.execute(select(RetainedOriginal).where(RetainedOriginal.job_id == job.id))).scalars())


@pytest.mark.parametrize("status", ["failed", "cancelled", "rejected"])
async def test_unsuccessful_jobs_never_schedule_a_deletion(db_settings, tmp_path: Path, status: str) -> None:  # noqa: ANN001
    assert await _job_with_result(tmp_path, status) == []


async def test_completed_job_schedules_the_reported_original(db_settings, tmp_path: Path) -> None:  # noqa: ANN001
    rows = await _job_with_result(tmp_path, "completed", original_disposition="kept", original_path=(tmp_path / "a.mp4").as_posix(), original_size=10, original_fingerprint="fp")
    assert len(rows) == 1
    entry = rows[0]
    assert entry.state == "pending" and entry.original_fingerprint == "fp"
    assert entry.due_at - entry.created_at == timedelta(days=7)
    assert entry.allowed_root == tmp_path.as_posix()


async def test_rescued_original_is_blocked_for_a_person_to_check(db_settings, tmp_path: Path) -> None:  # noqa: ANN001
    rows = await _job_with_result(tmp_path, "completed", original_disposition="rescued", original_path=(tmp_path / "a.original.mp4").as_posix())
    assert rows and rows[0].state == "blocked"


async def test_deleted_original_needs_no_entry(db_settings, tmp_path: Path) -> None:  # noqa: ANN001
    assert await _job_with_result(tmp_path, "completed", original_disposition="deleted") == []


async def test_a_later_stage_relinks_the_guarded_output(world: World) -> None:
    newer = world.root / "stream.ff.ff.mkv"
    async with sessionmaker()() as db:
        plan = FinalizePlan(source=str(world.output), temp_output=str(world.root / ".frameforge-tmp/j.mkv"), final_output=str(newer), original_action="keep")
        job = Job(source_path=str(world.output), profile_name="AV1", state="finalizing", finalize_plan=plan.model_dump())
        db.add(job)
        await db.flush()
        await record_result(db, job, JobResult(job_id=job.id, status="completed", final_path=str(newer), output_size=123, output_fingerprint="newfp"), max_attempts=1)
        await db.commit()
        entry = await db.get(RetainedOriginal, world.entry_id)
        assert entry is not None
        assert entry.output_path == str(newer) and entry.output_job_id == job.id and entry.output_fingerprint == "newfp"
