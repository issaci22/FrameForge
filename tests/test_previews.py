"""Compression previews: the node runner with real FFmpeg, wired to the server service through a fake socket."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from frameforge_node.preview import PreviewRunner
from frameforge_server.services import previews as previews_module
from frameforge_server.services.node_manager import NodeConnection, manager
from frameforge_server.services.previews import PreviewService, PreviewUnavailable
from frameforge_shared import protocol as P
from frameforge_shared.probe import probe_file
from frameforge_shared.profile import ProfileSpec

from .conftest import ALL_CPU, make_caps, make_clip, needs_ffmpeg

SPEC = ProfileSpec(video_codec="h264", speed="fast")


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)


def connection(node_id: int = 1, features: set[str] | None = None, max_concurrency: int = 1) -> NodeConnection:
    return NodeConnection(
        node_id=node_id,
        name=f"node{node_id}",
        ws=FakeSocket(),  # type: ignore[arg-type]
        caps=make_caps(ALL_CPU),
        max_concurrency=max_concurrency,
        paused=False,
        enabled=True,
        reserve_slot_for_normal=False,
        constraints={},
        path_mappings=[],
        features={P.FEATURE_PREVIEW} if features is None else features,
    )


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    class S:
        previews_dir = tmp_path / "cache"

    monkeypatch.setattr(previews_module, "get_settings", lambda: S)
    monkeypatch.setattr(manager, "connections", {})
    svc = PreviewService()
    svc.reset_cache()
    return svc


def last_request(conn: NodeConnection) -> P.PreviewRequest:
    msg = [m for m in conn.ws.sent if m["type"] == P.SERVER_PREVIEW][-1]  # type: ignore[attr-defined]
    return P.PreviewRequest.model_validate(msg["data"])


# ---------------------------------------------------------------------------
# End to end: server request → node runner (real FFmpeg) → server cache
# ---------------------------------------------------------------------------


@needs_ffmpeg
async def test_preview_round_trip(service: PreviewService, tmp_path: Path) -> None:
    src = make_clip(tmp_path / "stream.mp4", seconds=6.0, size="320x240")
    info = await probe_file(str(src))
    conn = connection()
    manager.connections[conn.node_id] = conn

    p = await service.start(file_id=1, source_path=str(src), filename="stream.mp4", source=info, spec=SPEC, node_id=None, positions=[0.5])
    assert conn.preview_busy and conn.free_slots == 0, "a running preview takes the node's slot"

    async def to_server(msg_type: str, data: Any) -> None:
        payload = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        await service.on_message(conn, msg_type, payload)

    runner = PreviewRunner(to_server, tmp_path / "node-state", lambda: [])
    runner.start(last_request(conn))
    await runner._task  # type: ignore[misc]

    assert p.state == "done", p.error
    assert not conn.preview_busy
    view = service.view(p)
    assert len(view["samples"]) == 1 and view["quality_label"] == "CRF 21"
    for kind in ("original", "encoded"):
        path = service.frame_path(p.id, 0, kind)
        assert path is not None and path.read_bytes().startswith(b"\x89PNG")
    est = view["estimate"]
    assert est["video_bytes_low"] <= est["video_bytes"] <= est["video_bytes_high"] and est["video_bytes"] > 0
    assert "audio not included" in est["basis"]
    assert not (tmp_path / "node-state" / "previews" / p.id).exists(), "the node cleans up its samples"
    assert src.exists()


@needs_ffmpeg
async def test_cancel_stops_the_node_and_sends_no_result(tmp_path: Path) -> None:
    src = make_clip(tmp_path / "stream.mp4", seconds=4.0)
    info = await probe_file(str(src))
    sent: list[str] = []

    async def send(msg_type: str, data: Any) -> None:
        sent.append(msg_type)

    runner = PreviewRunner(send, tmp_path / "state", lambda: [])
    req = P.PreviewRequest(request_id="abc", source_path=str(src), profile=SPEC, encoder=P.EncoderChoice(codec="h264", backend="cpu", encoder="libx264"), source_media=info, positions=[1.0, 2.0])
    runner.start(req)
    runner.cancel("abc")
    with pytest.raises(asyncio.CancelledError):
        await runner._task  # type: ignore[misc]
    assert P.NODE_PREVIEW_RESULT not in sent
    assert not (tmp_path / "state" / "previews" / "abc").exists()


async def test_a_second_request_is_refused_while_busy(tmp_path: Path) -> None:
    sent: list[tuple[str, Any]] = []

    async def send(msg_type: str, data: Any) -> None:
        sent.append((msg_type, data))

    runner = PreviewRunner(send, tmp_path, lambda: [])
    runner._task = asyncio.create_task(asyncio.sleep(10))
    runner.start(P.PreviewRequest(request_id="x", source_path="/nope", profile=SPEC, encoder=P.EncoderChoice(codec="h264", backend="cpu", encoder="libx264"), source_media=_media(), positions=[1.0]))
    await asyncio.sleep(0)
    runner._task.cancel()
    assert sent and sent[0][0] == P.NODE_PREVIEW_RESULT and not sent[0][1].ok


def _media():  # noqa: ANN202
    from .conftest import make_media

    return make_media()


# ---------------------------------------------------------------------------
# Server bookkeeping
# ---------------------------------------------------------------------------


async def test_old_nodes_and_busy_nodes_are_not_asked(service: PreviewService) -> None:
    manager.connections[1] = connection(1, features=set())
    busy = connection(2)
    busy.active_jobs = {99}
    manager.connections[2] = busy
    with pytest.raises(PreviewUnavailable) as exc:
        await service.start(file_id=1, source_path="/m/a.mkv", filename="a.mkv", source=_media(), spec=SPEC, node_id=None, positions=None)
    assert "newer FrameForge node" in str(exc.value) and "busy" in str(exc.value)


async def test_remux_has_nothing_to_preview(service: PreviewService) -> None:
    manager.connections[1] = connection()
    assert not service.availability(ProfileSpec(video_codec="copy"))["available"]


async def test_disconnect_fails_the_preview_and_frees_the_slot(service: PreviewService) -> None:
    conn = connection()
    manager.connections[1] = conn
    p = await service.start(file_id=1, source_path="/m/a.mkv", filename="a.mkv", source=_media(), spec=SPEC, node_id=None, positions=None)
    service.on_connection_closed(conn)
    assert p.state == "failed" and "disconnected" in (p.error or "")
    assert not conn.preview_busy


async def test_messages_from_a_replaced_connection_are_ignored(service: PreviewService) -> None:
    conn = connection()
    manager.connections[1] = conn
    p = await service.start(file_id=1, source_path="/m/a.mkv", filename="a.mkv", source=_media(), spec=SPEC, node_id=None, positions=None)
    stranger = connection()
    await service.on_message(stranger, P.NODE_PREVIEW_RESULT, {"request_id": p.id, "ok": False, "error": "x"})
    assert p.state == "running"


async def test_unexpected_file_names_are_rejected(service: PreviewService) -> None:
    conn = connection()
    manager.connections[1] = conn
    p = await service.start(file_id=1, source_path="/m/a.mkv", filename="a.mkv", source=_media(), spec=SPEC, node_id=None, positions=None)
    await service.on_message(conn, P.NODE_PREVIEW_CHUNK, {"request_id": p.id, "name": "../../etc/passwd", "offset": 0, "data": "", "last": True})
    assert p.state == "failed"


async def test_silent_node_times_out(service: PreviewService, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(previews_module, "INACTIVITY_TIMEOUT", 0.05)
    monkeypatch.setattr(previews_module, "WATCH_INTERVAL", 0.02)
    conn = connection()
    manager.connections[1] = conn
    p = await service.start(file_id=1, source_path="/m/a.mkv", filename="a.mkv", source=_media(), spec=SPEC, node_id=None, positions=None)
    for _ in range(50):
        if p.state != "running":
            break
        await asyncio.sleep(0.02)
    assert p.state == "failed" and "stopped responding" in (p.error or "")
    assert any(m["type"] == P.SERVER_PREVIEW_CANCEL for m in conn.ws.sent)  # type: ignore[attr-defined]
    assert not conn.preview_busy
