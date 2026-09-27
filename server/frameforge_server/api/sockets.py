"""WebSocket endpoints: UI live events and the node gateway."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..auth.sessions import websocket_user
from ..events import bus
from ..services.node_manager import manager

log = logging.getLogger(__name__)
router = APIRouter()

_UI_PING_SECONDS = 25


@router.websocket("/events")
async def ui_events(ws: WebSocket) -> None:
    user = await websocket_user(ws)
    if user is None:
        await ws.close(code=4401, reason="Not signed in")
        return
    await ws.accept()
    sub = bus.subscribe()

    async def pump_out() -> None:
        while True:
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=_UI_PING_SECONDS)
            except asyncio.TimeoutError:
                event = {"topic": "ping", "data": {}}
            await ws.send_json(event)

    async def pump_in() -> None:
        while True:
            await ws.receive_text()  # we don't expect client messages; this detects disconnects

    sender = asyncio.create_task(pump_out())
    receiver = asyncio.create_task(pump_in())
    try:
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(sub)
        for task in (sender, receiver):
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


@router.websocket("/node/connect")
async def node_gateway(ws: WebSocket) -> None:
    await manager.handle_socket(ws)
