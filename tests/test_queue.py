"""Queue management against a real database: Clear Queue, stop requests, re-queue holds, races and excluded folders.

Regression tests for the queue refilling itself:
- a cancelled file was queued again by the next scan;
- the scheduler could overwrite a cancel, and a cancel could overwrite the scheduler's claim;
- a job the user stopped came back when its node dropped, rejected it or failed transiently;
- two evaluations at once could queue the same file twice.
"""

from __future__ import annotations

import asyncio
import os
import posixpath
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from frameforge_server.api import files as files_api
from frameforge_server.api import jobs as jobs_api
from frameforge_server.api.libraries import LibraryIn, _plain_fields
from frameforge_server.db.models import Job, JobEvent, Library, MediaFile, MediaMetadata, Node, Profile, Rule
from frameforge_server.db.types import utcnow
from frameforge_server.services import exclusions, scanner, system_settings
from frameforge_server.services import jobs as job_service
from frameforge_server.services import queue as queue_service
from frameforge_server.services import scheduler as sched
from frameforge_server.services.node_manager import manager
from frameforge_server.services.system_settings import GeneralSettings
from frameforge_shared import protocol as P
from frameforge_shared.errors import diagnosis
from frameforge_shared.profile import ProfileSpec

from .conftest import make_media
from .test_scheduler import FakeSocket, conn

ROOT = "/media/vods"


@pytest.fixture
async def sm(db_settings, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    from frameforge_server.db.session import sessionmaker

    monkeypatch.setattr(manager, "connections", {})
    monkeypatch.setattr(manager, "progress", {})
    monkeypatch.setattr(system_settings, "_cache", GeneralSettings())
    monkeypatch.setattr(queue_service, "queue_lock", asyncio.Lock())  # one per test's event loop
    async with sessionmaker()() as db:
        db.add(Library(id=1, name="VODs", paths=[ROOT]))
        db.add(Profile(id=1, name="H.265", spec=ProfileSpec().model_dump(mode="json")))
        for nid in (1, 2):
            db.add(Node(id=nid, name=f"node-{nid}", token_hash="x" * 64, token_hint="ffn_…"))
        await db.flush()
        db.add(Rule(name="Everything", conditions={}, action="transcode", profile_id=1, priority=2, skip_if_target_codec=False))
        await db.commit()
    return sessionmaker()


def _meta() -> MediaMetadata:
    return MediaMetadata(container="mkv", duration=600.0, video_codec="h264", width=1920, height=1080, short_side=1080, fps=60.0, audio_codecs=["aac"], audio_count=1, info=make_media().model_dump(mode="json"))


async def add_file(sm, path: str, status: str = "ready", decision: str | None = None) -> int:  # noqa: ANN001
    async with sm() as db:
        f = MediaFile(
            library_id=1,
            path=path,
            relative_path=posixpath.relpath(path, ROOT),
            filename=posixpath.basename(path),
            extension=".mkv",
            size=1000,
            mtime=utcnow(),
            status=status,
            decision=decision,
            meta=_meta(),
        )
        db.add(f)
        await db.commit()
        return f.id


async def add_job(sm, path: str, state: str = "queued", node_id: int | None = None, **kw: Any) -> int:  # noqa: ANN001
    async with sm() as db:
        file = (await db.execute(select(MediaFile).where(MediaFile.path == path))).scalar_one_or_none()
        j = Job(
            file_id=file.id if file else None,
            library_id=1,
            source_path=path,
            profile_id=1,
            profile_name="H.265",
            profile_spec=ProfileSpec().model_dump(mode="json"),
            state=state,
            node_id=node_id,
            node_name=f"node-{node_id}" if node_id else None,
            **kw,
        )
        db.add(j)
        if file is not None and state == "queued":
            file.status = "queued"
        elif file is not None and state not in ("completed", "failed", "cancelled"):
            file.status = "processing"
        await db.commit()
        return j.id


async def jobs(sm) -> dict[int, Job]:  # noqa: ANN001
    async with sm() as db:
        return {j.id: j for j in (await db.execute(select(Job))).scalars()}


async def file_status(sm, path: str) -> str:  # noqa: ANN001
    async with sm() as db:
        return (await db.execute(select(MediaFile.status).where(MediaFile.path == path))).scalar_one()


async def evaluate(sm) -> int:  # noqa: ANN001
    async with sm() as db:
        return await scanner.evaluate_library(db, 1)


async def clear(sm, stop_running: bool = False) -> dict[str, int]:  # noqa: ANN001
    async with sm() as db:
        return await queue_service.clear_queue(db, stop_running=stop_running)


def sent_types(c) -> list[tuple[str, int]]:  # noqa: ANN001
    return [(m["type"], m["data"]["job_id"]) for m in c.ws.sent]


# ---------------------------------------------------------------------------
# Clear Queue
# ---------------------------------------------------------------------------


async def test_clear_queue_cancels_every_pending_job_and_keeps_history(sm) -> None:  # noqa: ANN001
    paths = [f"{ROOT}/stream-{i}.mkv" for i in range(3)]
    for p in paths:
        await add_file(sm, p)
    assert await evaluate(sm) == 3
    done = await add_job(sm, f"{ROOT}/done.mkv", state="completed")
    failed = await add_job(sm, f"{ROOT}/broken.mkv", state="failed")

    summary = await clear(sm)

    assert summary["cancelled"] == 3 and summary["still_running"] == 0
    after = await jobs(sm)
    pending = [j for j in after.values() if j.source_path in paths]
    assert all(j.state == "cancelled" and j.cancel_requested_at is not None and j.finished_at is not None for j in pending)
    assert after[done].state == "completed" and after[failed].state == "failed"  # history untouched
    assert [await file_status(sm, p) for p in paths] == ["ready"] * 3
    async with sm() as db:
        kinds = (await db.execute(select(JobEvent.job_id, JobEvent.kind).where(JobEvent.kind == "cancelled"))).all()
    assert sorted(job_id for job_id, _ in kinds) == sorted(j.id for j in pending)


async def test_clearing_an_empty_queue_is_safe(sm) -> None:  # noqa: ANN001
    assert await clear(sm) == {"cancelled": 0, "stopping": 0, "stopping_offline": 0, "still_running": 0, "finalizing": 0}
    assert await clear(sm, stop_running=True) == {"cancelled": 0, "stopping": 0, "stopping_offline": 0, "still_running": 0, "finalizing": 0}


async def test_clear_queue_leaves_running_jobs_alone_by_default(sm) -> None:  # noqa: ANN001
    manager.connections[1] = conn(1)
    await add_file(sm, f"{ROOT}/running.mkv")
    running = await add_job(sm, f"{ROOT}/running.mkv", state="transcoding", node_id=1)
    await add_file(sm, f"{ROOT}/pending.mkv")
    await add_job(sm, f"{ROOT}/pending.mkv")

    summary = await clear(sm)

    assert summary["cancelled"] == 1 and summary["still_running"] == 1 and summary["stopping"] == 0
    j = (await jobs(sm))[running]
    assert j.state == "transcoding" and j.cancel_requested_at is None
    assert manager.connections[1].ws.sent == []  # type: ignore[attr-defined]


async def test_clear_queue_can_stop_running_jobs(sm) -> None:  # noqa: ANN001
    manager.connections[1] = conn(1, max_concurrency=2)  # node 2 is offline
    online = await add_job(sm, f"{ROOT}/a.mkv", state="transcoding", node_id=1)
    finalizing = await add_job(sm, f"{ROOT}/b.mkv", state="finalizing", node_id=1)
    offline = await add_job(sm, f"{ROOT}/c.mkv", state="transcoding", node_id=2)

    summary = await clear(sm, stop_running=True)

    assert summary == {"cancelled": 0, "stopping": 1, "stopping_offline": 1, "still_running": 1, "finalizing": 1}
    assert sent_types(manager.connections[1]) == [(P.SERVER_CANCEL, online)]
    after = await jobs(sm)
    # Nothing claims to be cancelled until the node confirms.
    assert after[online].state == "transcoding" and after[online].cancel_requested_at is not None and after[online].waiting_reason == "Stopping…"
    assert after[offline].state == "transcoding" and after[offline].waiting_reason == "Stops when node-2 reconnects"
    assert after[finalizing].cancel_requested_at is None


async def test_concurrent_clears_cancel_each_job_once(sm) -> None:  # noqa: ANN001
    for i in range(10):
        await add_job(sm, f"{ROOT}/s{i}.mkv")
    a, b = await asyncio.gather(clear(sm), clear(sm))
    assert a["cancelled"] + b["cancelled"] == 10
    async with sm() as db:
        per_job = (await db.execute(select(JobEvent.job_id, func.count()).where(JobEvent.kind == "cancelled").group_by(JobEvent.job_id))).all()
    assert len(per_job) == 10 and all(n == 1 for _, n in per_job)


# ---------------------------------------------------------------------------
# Re-queue hold after a cancel by the user
# ---------------------------------------------------------------------------


async def test_scans_do_not_requeue_cleared_files_until_retried(sm) -> None:  # noqa: ANN001
    a, b = f"{ROOT}/a.mkv", f"{ROOT}/b.mkv"
    await add_file(sm, a)
    await add_file(sm, b)
    assert await evaluate(sm) == 2
    await clear(sm)

    assert await evaluate(sm) == 0
    assert await evaluate(sm) == 0  # repeated scans don't bring them back
    async with sm() as db:
        decision = (await db.execute(select(MediaFile.decision).where(MediaFile.path == a))).scalar_one()
    assert "You cancelled the last “H.265” job" in decision

    # Retrying one queues it again; the other stays held.
    cancelled_a = next(j for j in (await jobs(sm)).values() if j.source_path == a)
    async with sm() as db:
        await jobs_api.retry_job(cancelled_a.id, db)
    assert sorted(j.source_path for j in (await jobs(sm)).values() if j.state == "queued") == [a]

    # Clearing the cancelled history lifts the hold, like it does for failed jobs.
    async with sm() as db:
        await jobs_api.clear_jobs(jobs_api.ClearRequest(states=["cancelled"]), db)
    assert await evaluate(sm) == 1


async def test_cancelling_one_queued_job_holds_it_too(sm) -> None:  # noqa: ANN001
    await add_file(sm, f"{ROOT}/a.mkv")
    assert await evaluate(sm) == 1
    (job_id,) = (await jobs(sm)).keys()
    async with sm() as db:
        view = await jobs_api.cancel_job(job_id, db)
    assert view["state"] == "cancelled" and view["cancel_requested"] is True
    assert await evaluate(sm) == 0


async def test_a_cancel_from_a_node_shutdown_still_requeues(sm) -> None:  # noqa: ANN001
    path = f"{ROOT}/a.mkv"
    await add_file(sm, path)
    job_id = await add_job(sm, path, state="transcoding", node_id=1)
    async with sm() as db:
        job = await db.get(Job, job_id)
        await job_service.record_result(db, job, P.JobResult(job_id=job_id, status="cancelled"), max_attempts=3)
        await db.commit()
    assert (await jobs(sm))[job_id].state == "cancelled"
    assert await evaluate(sm) == 1  # nobody asked for this cancel: the file is queued again as before


# ---------------------------------------------------------------------------
# A stopped job never comes back
# ---------------------------------------------------------------------------

TRANSIENT = diagnosis("nvenc_session_limit", "The NVIDIA encoder is busy", "x")
FATAL = diagnosis("validation_failed", "The output didn't pass validation", "x")


@pytest.mark.parametrize(
    ("result", "stopped", "expected"),
    [
        (P.JobResult(job_id=0, status="rejected", reject_reason="busy"), True, "cancelled"),
        (P.JobResult(job_id=0, status="failed", diagnosis=TRANSIENT), True, "cancelled"),
        (P.JobResult(job_id=0, status="failed", diagnosis=FATAL), True, "cancelled"),
        (P.JobResult(job_id=0, status="cancelled"), True, "cancelled"),
        (P.JobResult(job_id=0, status="completed", final_path=f"{ROOT}/a.mkv", output_size=10), True, "completed"),
        # Unchanged behavior without a stop request:
        (P.JobResult(job_id=0, status="rejected", reject_reason="busy"), False, "queued"),
        (P.JobResult(job_id=0, status="failed", diagnosis=TRANSIENT), False, "queued"),
        (P.JobResult(job_id=0, status="failed", diagnosis=FATAL), False, "failed"),
    ],
)
async def test_results_for_a_stopped_job(sm, result: P.JobResult, stopped: bool, expected: str) -> None:  # noqa: ANN001
    path = f"{ROOT}/a.mkv"
    await add_file(sm, path)
    job_id = await add_job(sm, path, state="transcoding", node_id=1, cancel_requested_at=utcnow() if stopped else None, waiting_reason="Stopping…" if stopped else None)
    async with sm() as db:
        job = await db.get(Job, job_id)
        await job_service.record_result(db, job, result.model_copy(update={"job_id": job_id}), max_attempts=3)
        await db.commit()
    after = (await jobs(sm))[job_id]
    assert after.state == expected
    if expected != "queued":  # a finished job keeps no stale "Stopping…" (seen in the e2e run)
        assert after.waiting_reason is None
    if expected == "cancelled":
        assert await file_status(sm, path) == "ready"


async def test_node_going_offline_does_not_requeue_a_stopped_job(sm) -> None:  # noqa: ANN001
    long_ago = utcnow() - timedelta(hours=1)
    async with sm() as db:
        (await db.get(Node, 2)).last_seen_at = long_ago
        await db.commit()
    manager.started_at = long_ago
    stopped = await add_job(sm, f"{ROOT}/a.mkv", state="transcoding", node_id=2, assigned_at=long_ago, cancel_requested_at=utcnow())
    normal = await add_job(sm, f"{ROOT}/b.mkv", state="transcoding", node_id=2, assigned_at=long_ago)
    await manager.reconcile()
    after = await jobs(sm)
    assert after[stopped].state == "cancelled"
    assert after[normal].state == "queued"  # unchanged behavior


async def test_reconnecting_node_is_told_to_stop_and_lost_stopped_jobs_end_cancelled(sm) -> None:  # noqa: ANN001
    still_running = await add_job(sm, f"{ROOT}/a.mkv", state="transcoding", node_id=1, cancel_requested_at=utcnow())
    lost = await add_job(sm, f"{ROOT}/b.mkv", state="transcoding", node_id=1, cancel_requested_at=utcnow())
    c = conn(1, active_jobs={still_running})
    await manager._reconcile_on_connect(c)
    assert (P.SERVER_CANCEL, still_running) in sent_types(c)
    after = await jobs(sm)
    assert after[still_running].state == "transcoding"
    assert after[lost].state == "cancelled"


# ---------------------------------------------------------------------------
# Races between the scheduler, cancels and evaluations
# ---------------------------------------------------------------------------


async def test_scheduler_does_not_start_a_job_cancelled_after_it_was_read(sm) -> None:  # noqa: ANN001
    c = conn(1)
    manager.connections[1] = c
    job_id = await add_job(sm, f"{ROOT}/a.mkv")
    async with sm() as scheduler_db:
        stale = await scheduler_db.get(Job, job_id)  # the scheduler's tick read it as queued...
        async with sm() as api_db:  # ...then the user cancelled it
            assert await queue_service.cancel_queued(api_db, job_ids=[job_id], message="x", by_user=True) == [job_id]
            await api_db.commit()
        assert await sched.Scheduler()._try_place(scheduler_db, stale, [c], GeneralSettings(), {}) is None
        await scheduler_db.commit()
    assert (await jobs(sm))[job_id].state == "cancelled"
    assert c.ws.sent == [] and c.pending_assignments == {}  # type: ignore[attr-defined]


async def test_cancel_does_not_overwrite_a_job_the_scheduler_claimed(sm) -> None:  # noqa: ANN001
    job_id = await add_job(sm, f"{ROOT}/a.mkv")
    async with sm() as api_db:
        await api_db.get(Job, job_id)  # the API read it as queued...
        async with sm() as scheduler_db:  # ...then the scheduler claimed it
            (await scheduler_db.get(Job, job_id)).state = "assigned"
            await scheduler_db.commit()
        assert await queue_service.cancel_queued(api_db, job_ids=[job_id], message="x", by_user=True) == []
        await api_db.commit()
    assert (await jobs(sm))[job_id].state == "assigned"


async def test_cancel_endpoint_stops_a_job_that_was_claimed_meanwhile(sm) -> None:  # noqa: ANN001
    c = conn(1)
    manager.connections[1] = c
    job_id = await add_job(sm, f"{ROOT}/a.mkv", state="assigned", node_id=1)
    async with sm() as db:
        view = await jobs_api.cancel_job(job_id, db)
    assert view["state"] == "assigned" and view["cancel_requested"] is True and view["waiting_reason"] == "Stopping…"
    assert sent_types(c) == [(P.SERVER_CANCEL, job_id)]


async def test_finalizing_jobs_cannot_be_cancelled(sm) -> None:  # noqa: ANN001
    job_id = await add_job(sm, f"{ROOT}/a.mkv", state="finalizing", node_id=1)
    async with sm() as db:
        with pytest.raises(HTTPException) as exc:
            await jobs_api.cancel_job(job_id, db)
    assert exc.value.status_code == 409
    assert (await jobs(sm))[job_id].cancel_requested_at is None


async def test_concurrent_evaluations_queue_each_file_once(sm) -> None:  # noqa: ANN001
    # Decisions already recorded, as after any earlier scan: nothing else makes the second evaluation wait
    # for the first one's write lock, so both would see "no job yet" for the same file.
    for i in range(5):
        await add_file(sm, f"{ROOT}/s{i}.mkv", decision="Rule “Everything” → H.265 (Normal priority)")
    created = await asyncio.gather(evaluate(sm), evaluate(sm))
    assert sum(created) == 5
    per_file = [j.file_id for j in (await jobs(sm)).values()]
    assert len(per_file) == len(set(per_file)) == 5


# ---------------------------------------------------------------------------
# Excluded folders and the queue
# ---------------------------------------------------------------------------


def test_library_input_normalizes_excluded_folders() -> None:
    body = LibraryIn(name="VODs", paths=["/media/vods/"], exclude_folders=["Archive", "/media/vods/archive/", "\\media\\vods\\Raw Takes", ""])
    assert body.exclude_folders == ["/media/vods/Archive", "/media/vods/Raw Takes"]
    with pytest.raises(ValidationError, match="isn't inside"):
        LibraryIn(name="VODs", paths=["/media/vods"], exclude_folders=["/media/vods/../secret"])
    with pytest.raises(ValidationError, match="library folder itself"):
        LibraryIn(name="VODs", paths=["/media/vods"], exclude_folders=["/media/vods"])
    # An older client that doesn't send the field must not wipe the existing exclusions on save.
    assert "exclude_folders" not in _plain_fields(LibraryIn(name="VODs", paths=["/media/vods"]))
    assert _plain_fields(LibraryIn(name="VODs", paths=["/media/vods"], exclude_folders=[]))["exclude_folders"] == []


async def set_exclusions(sm, folders: list[str]) -> dict[str, Any]:  # noqa: ANN001
    async with sm() as db:
        lib = await db.get(Library, 1)
        lib.exclude_folders = folders
        summary = await exclusions.apply(db, lib)
        await db.commit()
        return summary


async def test_excluding_a_folder_cancels_its_queued_jobs_but_not_running_ones(sm) -> None:  # noqa: ANN001
    queued, running, idle, keep = f"{ROOT}/Archive/a.mkv", f"{ROOT}/Archive/b.mkv", f"{ROOT}/Archive/c.mkv", f"{ROOT}/Archive2/keep.mkv"
    for p in (queued, running, idle, keep):
        await add_file(sm, p)
    q_job = await add_job(sm, queued)
    r_job = await add_job(sm, running, state="transcoding", node_id=1)
    k_job = await add_job(sm, keep)

    summary = await set_exclusions(sm, [f"{ROOT}/Archive"])

    assert summary == {"queued_cancelled": 1, "running": 1, "files_excluded": 2, "files_restored": 0}
    after = await jobs(sm)
    assert after[q_job].state == "cancelled" and after[q_job].cancel_requested_at is None
    assert after[r_job].state == "transcoding"  # never interrupted by an exclusion
    assert after[k_job].state == "queued"  # Archive2 is a different folder
    assert [await file_status(sm, p) for p in (queued, running, idle, keep)] == ["excluded", "processing", "excluded", "queued"]
    assert await evaluate(sm) == 0

    # Removing the exclusion restores the files, and rules may queue them again (no user cancel to respect).
    summary = await set_exclusions(sm, [])
    assert summary["files_restored"] == 2
    assert [await file_status(sm, p) for p in (queued, idle)] == ["ready", "ready"]
    assert await evaluate(sm) == 2


async def test_manual_queue_and_retry_refuse_excluded_files(sm) -> None:  # noqa: ANN001
    path = f"{ROOT}/Archive/a.mkv"
    file_id = await add_file(sm, path)
    old = await add_job(sm, path, state="cancelled")
    await set_exclusions(sm, [f"{ROOT}/Archive"])
    async with sm() as db:
        with pytest.raises(HTTPException) as exc:
            await files_api.queue_file(file_id, files_api.QueueRequest(profile_id=1), db)
        assert exc.value.status_code == 409 and "excluded folder" in exc.value.detail
        with pytest.raises(HTTPException) as exc:
            await jobs_api.retry_job(old, db)
        assert exc.value.status_code == 409
    assert [j.id for j in (await jobs(sm)).values()] == [old]


async def test_manual_queue_checks_the_path_even_before_a_scan_marks_it(sm) -> None:  # noqa: ANN001
    file_id = await add_file(sm, f"{ROOT}/Archive/a.mkv")
    async with sm() as db:
        lib = await db.get(Library, 1)
        lib.exclude_folders = [f"{ROOT}/Archive"]  # saved without applying (e.g. mid-scan)
        await db.commit()
    async with sm() as db:
        with pytest.raises(HTTPException) as exc:
            await files_api.queue_file(file_id, files_api.QueueRequest(profile_id=1), db)
    assert exc.value.status_code == 409


async def test_scheduler_never_starts_a_job_in_an_excluded_folder(sm) -> None:  # noqa: ANN001
    c = conn(1)
    manager.connections[1] = c
    path = f"{ROOT}/Archive/a.mkv"
    await add_file(sm, path)
    job_id = await add_job(sm, path)
    async with sm() as db:
        (await db.get(Library, 1)).exclude_folders = [f"{ROOT}/archive"]  # case differs on purpose
        await db.commit()
    await sched.Scheduler().tick()
    assert (await jobs(sm))[job_id].state == "cancelled"
    assert await file_status(sm, path) == "excluded"
    assert c.ws.sent == []  # type: ignore[attr-defined]
    async with sm() as db:
        events = list((await db.execute(select(JobEvent.message).where(JobEvent.job_id == job_id))).scalars())
    assert any("excluded" in e for e in events)


# ---------------------------------------------------------------------------
# Full scans (POSIX paths: run in the container)
# ---------------------------------------------------------------------------

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="server paths are POSIX")


@posix_only
async def test_scans_skip_excluded_folders_and_keep_them_skipped(sm, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    async def fake_probe(path: str):  # noqa: ANN202
        return make_media()

    monkeypatch.setattr(scanner, "probe_file", fake_probe)
    monkeypatch.setattr(scanner, "STABLE_SECONDS", -1)
    root = tmp_path.as_posix()
    for rel in ("keep.mkv", "Archive/old.mkv", "Archive/deep/older.mkv", "Archive2/also-keep.mkv"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(os.urandom(64))
    async with sm() as db:
        lib = await db.get(Library, 1)
        lib.paths = [root]
        lib.exclude_folders = [f"{root}/Archive"]
        await db.commit()

    for _ in range(3):  # repeated scans never bring excluded files in
        await scanner.scan_library(1)
        async with sm() as db:
            paths = set((await db.execute(select(MediaFile.path))).scalars())
            queued = {j.source_path for j in (await db.execute(select(Job))).scalars()}
        assert paths == queued == {f"{root}/keep.mkv", f"{root}/Archive2/also-keep.mkv"}

    # Excluding a folder whose files were already found marks them "excluded", not "missing".
    async with sm() as db:
        lib = await db.get(Library, 1)
        lib.exclude_folders = [f"{root}/Archive", f"{root}/Archive2"]
        await db.commit()
    await clear(sm)
    summary = await scanner.scan_library(1)
    assert summary["excluded"] == 1 and summary["missing"] == 0
    assert await file_status(sm, f"{root}/Archive2/also-keep.mkv") == "excluded"
