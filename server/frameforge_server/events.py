"""In-process pub/sub used to push live updates to UI websockets."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_QUEUE_SIZE = 1000


@dataclass(eq=False)  # identity-hashed so subscribers can live in a set
class Subscriber:
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_SIZE))
    dropped: int = 0


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()

    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        """Non-blocking. Slow subscribers lose events rather than stalling the server."""
        event = {"topic": topic, "data": data, "ts": time.time()}
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait(event)
            except asyncio.QueueFull:
                sub.dropped += 1

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


bus = EventBus()
