"""Scheduler eligibility: node blocks, time windows, quiet hours, placement and commit-before-send."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select

from frameforge_server.db.models import Job, Library, Node
from frameforge_server.services import scheduler as sched
from frameforge_server.services import system_settings, timewindow
from frameforge_server.services.node_manager import NodeConnection, manager
from frameforge_server.services.system_settings import GeneralSettings, QuietHours
from frameforge_shared import protocol as P

from .conftest import ALL_CPU, make_caps

# Thursday 2026-09-17
DAY = datetime(2026, 9, 17, 14, 0)
NIGHT = datetime(2026, 9, 17, 2, 0)
THU, FRI = 3, 4


class FakeSocket:
    def __init__(self, on_send: Any = None, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.on_send = on_send
        self.fail = fail

    async def send_json(self, msg: dict[str, Any]) -> None:
        if self.fail:
            raise ConnectionError("socket closed")
        if self.on_send:
            await self.on_send(msg)
        self.sent.append(msg)


def conn(node_id: int = 1, *, caps: P.NodeCapabilities | None = None, ws: FakeSocket | None = None, **kw: Any) -> NodeConnection:
    return NodeConnection(
        node_id=node_id,
        name=kw.pop("name", f"node-{node_id}"),
        ws=ws or FakeSocket(),  # type: ignore[arg-type]
        caps=caps or make_caps(ALL_CPU),
        max_concurrency=kw.pop("max_concurrency", 1),
        paused=kw.pop("paused", False),
        enabled=kw.pop("enabled", True),
        reserve_slot_for_normal=kw.pop("reserve_slot_for_normal", False),
        constraints=kw.pop("constraints", {}),
        path_mappings=[],
        **kw,
    )


def job(**kw: Any) -> Job:
    return Job(id=kw.pop("id", 1), priority=kw.pop("priority", 2), manual=kw.pop("manual", False), excluded_nodes=kw.pop("excluded_nodes", []), schedule=kw.pop("schedule", {}), **kw)


def metrics(cpu: float = 5.0, gpu: float | None = None) -> P.NodeMetrics:
    gpus = [P.GpuMetrics(index=0, utilization=gpu)] if gpu is not None else []
    return P.NodeMetrics(timestamp=0, cpu_percent=cpu, ram_used_mb=0, ram_total_mb=0, gpus=gpus)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Pin the scheduler's notion of local time."""

    class Clock:
        now = DAY

    monkeypatch.setattr(sched, "in_window", lambda window, now=None: timewindow.in_window(window, now or Clock.now))
    return Clock


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("window", "now", "inside"),
    [
        (None, DAY, True),
        ({"start": "09:00", "end": "17:00"}, DAY, True),
        ({"start": "09:00", "end": "17:00"}, NIGHT, False),
        ({"start": "23:00", "end": "07:00"}, NIGHT, True),
        ({"start": "23:00", "end": "07:00"}, DAY, False),
        ({"start": "23:00", "end": "07:00"}, datetime(2026, 9, 17, 7, 0), False),  # end is exclusive
        ({"start": "00:00", "end": "00:00"}, DAY, True),  # same start/end = all day
        ({"start": "09:00", "end": "17:00", "days": [THU]}, DAY, True),
        ({"start": "09:00", "end": "17:00", "days": [FRI]}, DAY, False),
        # Wrapped windows belong to the day they START: "Wed 23:00–07:00" covers Thu 02:00...
        ({"start": "23:00", "end": "07:00", "days": [2]}, NIGHT, True),
        # ...and "Thu 23:00–07:00" does not.
        ({"start": "23:00", "end": "07:00", "days": [THU]}, NIGHT, False),
        ({"start": "09:00", "end": "17:00", "days": []}, DAY, True),
    ],
)
def test_in_window(window: dict[str, Any] | None, now: datetime, inside: bool) -> None:
    assert timewindow.in_window(window, now) is inside


# ---------------------------------------------------------------------------
# Job-level blocks
# ---------------------------------------------------------------------------

NIGHTLY = {"enabled": True, "window": {"start": "23:00", "end": "07:00"}}


def test_job_schedule_window(clock) -> None:  # noqa: ANN001
    j = job(schedule={"window": {"start": "01:00", "end": "06:00"}})
    assert "Scheduled to run 01:00–06:00" == sched._job_window_block(j, GeneralSettings())
    clock.now = datetime(2026, 9, 17, 3, 0)
    assert sched._job_window_block(j, GeneralSettings()) is None


@pytest.mark.parametrize(
    ("applies_to", "priority", "blocked"),
    [
        ("background", 0, True),
        ("background", 1, False),
        ("background", 2, False),
        ("low_and_below", 0, True),
        ("low_and_below", 1, True),
        ("low_and_below", 2, False),
    ],
)
def test_quiet_hours_hold_low_priority_work_during_the_day(clock, applies_to: str, priority: int, blocked: bool) -> None:  # noqa: ANN001
    settings = GeneralSettings(quiet_hours=QuietHours(**NIGHTLY, applies_to=applies_to))
    assert (sched._job_window_block(job(priority=priority), settings) is not None) is blocked
    clock.now = NIGHT
    assert sched._job_window_block(job(priority=priority), settings) is None


def test_manual_jobs_ignore_quiet_hours(clock) -> None:  # noqa: ANN001
    settings = GeneralSettings(quiet_hours=QuietHours(**NIGHTLY))
    assert sched._job_window_block(job(priority=0, manual=True), settings) is None


# ---------------------------------------------------------------------------
# Node-level blocks
# ---------------------------------------------------------------------------


def test_eligible_node(clock) -> None:  # noqa: ANN001
    assert sched._node_block(conn(), job()) is None


@pytest.mark.parametrize(
    ("node_kw", "job_kw", "reason"),
    [
        ({"enabled": False}, {}, "disabled"),
        ({"paused": True}, {}, "paused"),
        ({}, {"excluded_nodes": [1]}, "declined"),
        ({"active_jobs": {99}}, {}, "all slots busy"),
        ({"pending_assignments": {98: 0.0}}, {}, "all slots busy"),
        ({"constraints": {"window": {"start": "23:00", "end": "07:00"}}}, {}, "only works 23:00–07:00"),
    ],
)
def test_node_blocks(clock, node_kw: dict[str, Any], job_kw: dict[str, Any], reason: str) -> None:  # noqa: ANN001
    assert reason in (sched._node_block(conn(**node_kw), job(**job_kw)) or "")


def test_last_slot_reserved_for_normal_priority(clock) -> None:  # noqa: ANN001
    c = conn(max_concurrency=2, reserve_slot_for_normal=True, active_jobs={50})
    assert "reserved" in (sched._node_block(c, job(priority=1)) or "")
    assert sched._node_block(c, job(priority=2)) is None
    # With both slots free, low priority work may take one.
    assert sched._node_block(conn(max_concurrency=2, reserve_slot_for_normal=True), job(priority=0)) is None


def test_utilization_limits_apply_only_while_node_is_idle(clock) -> None:  # noqa: ANN001
    limits = {"max_gpu_util": 50, "max_cpu_util": 80}
    gaming = conn(max_concurrency=2, constraints=limits, metrics=metrics(cpu=30, gpu=95))
    assert "GPU busy" in (sched._node_block(gaming, job()) or "")
    busy_cpu = conn(max_concurrency=2, constraints=limits, metrics=metrics(cpu=95))
    assert "CPU busy" in (sched._node_block(busy_cpu, job()) or "")
    # Our own job is what's using the GPU: that must not block the second slot.
    ours = conn(max_concurrency=2, constraints=limits, metrics=metrics(cpu=95, gpu=95), active_jobs={7})
    assert sched._node_block(ours, job()) is None


def test_unreported_gpu_utilization_does_not_block(clock) -> None:  # noqa: ANN001
    c = conn(constraints={"max_gpu_util": 50}, metrics=metrics(gpu=None))
    assert sched._node_block(c, job()) is None


# ---------------------------------------------------------------------------
# Placement through Scheduler.tick() against a real database
# ---------------------------------------------------------------------------


@pytest.fixture
async def world(db_settings, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN001, ANN201
    from frameforge_server.db.session import sessionmaker

    monkeypatch.setattr(manager, "connections", {})
    monkeypatch.setattr(system_settings, "_cache", GeneralSettings())
    async with sessionmaker()() as db:
        db.add(Library(id=1, name="VODs", paths=["/media/vods"], output_policy="backup"))
        for nid in (1, 2):
            db.add(Node(id=nid, name=f"node-{nid}", token_hash="x" * 64, token_hint="ffn_…"))
        await db.commit()
    return sessionmaker()


async def add_jobs(sm, *specs: dict[str, Any]) -> list[int]:  # noqa: ANN001
    ids = []
    async with sm() as db:
        for i, kw in enumerate(specs):
            j = Job(
                library_id=1,
                source_path=kw.pop("source_path", f"/media/vods/stream-{i}.mkv"),
                profile_id=1,
                profile_name="H.265",
                profile_spec=kw.pop("profile_spec", {"video_codec": "hevc"}),
                **kw,
            )
            db.add(j)
            await db.flush()
            ids.append(j.id)
        await db.commit()
    return ids


async def states(sm) -> dict[int, tuple[str, int | None, str | None]]:  # noqa: ANN001
    async with sm() as db:
        return {j.id: (j.state, j.node_id, j.waiting_reason) for j in (await db.execute(select(Job))).scalars()}


async def test_no_nodes_online_is_explained(world) -> None:  # noqa: ANN001
    (jid,) = await add_jobs(world, {})
    await sched.Scheduler().tick()
    assert (await states(world))[jid] == ("queued", None, "No nodes are online")


async def test_assignment_is_committed_before_it_is_sent(world) -> None:  # noqa: ANN001
    seen: list[str] = []

    async def on_send(msg: dict[str, Any]) -> None:
        # A different session, like the node's first job.stage message would use.
        async with world() as db:
            job_row = await db.get(Job, msg["data"]["job_id"])
            seen.append(job_row.state)

    manager.connections[1] = conn(1, ws=FakeSocket(on_send))
    (jid,) = await add_jobs(world, {})
    await sched.Scheduler().tick()
    assert seen == ["assigned"]
    assert (await states(world))[jid][:2] == ("assigned", 1)
    sent = manager.connections[1].ws.sent[0]  # type: ignore[attr-defined]
    assert sent["type"] == P.SERVER_ASSIGN
    assignment = P.JobAssignment.model_validate(sent["data"])
    assert assignment.encoder.encoder == "libx265"
    assert "/.frameforge-tmp/" in assignment.plan.temp_output
    assert assignment.frameforge_tag == f"job={jid};profile=1;v=1"


async def test_priority_order_then_fifo(world) -> None:  # noqa: ANN001
    manager.connections[1] = conn(1)
    low, high_a, high_b = await add_jobs(world, {"priority": 1}, {"priority": 3}, {"priority": 3})
    await sched.Scheduler().tick()
    st = await states(world)
    assert st[high_a][0] == "assigned"
    assert st[high_b][0] == "queued" and st[low][0] == "queued"
    assert st[low][2] == "Waiting for a free node slot"


async def test_gpu_node_preferred_over_cpu_node(world) -> None:  # noqa: ANN001
    manager.connections[1] = conn(1, caps=make_caps(ALL_CPU))
    manager.connections[2] = conn(2, caps=make_caps([*ALL_CPU, ("hevc_qsv", "hevc", "qsv")], render_device="/dev/dri/renderD129"))
    (jid,) = await add_jobs(world, {})
    await sched.Scheduler().tick()
    assert (await states(world))[jid][:2] == ("assigned", 2)
    assignment = P.JobAssignment.model_validate(manager.connections[2].ws.sent[0]["data"])  # type: ignore[attr-defined]
    assert assignment.encoder.backend == "qsv"
    assert assignment.encoder.device == "/dev/dri/renderD129"


async def test_node_without_a_suitable_encoder_is_skipped_with_reason(world) -> None:  # noqa: ANN001
    manager.connections[1] = conn(1, caps=make_caps([("libx264", "h264", "cpu")]))
    (jid,) = await add_jobs(world, {"profile_spec": {"video_codec": "av1"}})
    await sched.Scheduler().tick()
    state, node, reason = (await states(world))[jid]
    assert state == "queued" and node is None
    assert "node-1: No working AV1 encoder" in (reason or "")


async def test_failed_send_requeues_without_counting_an_attempt(world) -> None:  # noqa: ANN001
    manager.connections[1] = conn(1, ws=FakeSocket(fail=True))
    (jid,) = await add_jobs(world, {})
    await sched.Scheduler().tick()
    async with world() as db:
        j = await db.get(Job, jid)
        assert j.state == "queued"
        assert j.attempts == 0
        assert "Couldn't reach node-1" in (j.waiting_reason or "")
    assert manager.connections[1].pending_assignments == {}


async def test_non_queued_jobs_are_left_alone(world) -> None:  # noqa: ANN001
    manager.connections[1] = conn(1)
    (jid,) = await add_jobs(world, {"state": "finalizing", "node_id": 2})
    await sched.Scheduler().tick()
    assert (await states(world))[jid][:2] == ("finalizing", 2)
    assert manager.connections[1].ws.sent == []  # type: ignore[attr-defined]
