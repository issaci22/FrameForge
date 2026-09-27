"""Local-time window checks (container TZ applies)."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any


def _parse(hhmm: str) -> time:
    h, m = hhmm.split(":", 1)
    return time(int(h) % 24, int(m) % 60)


def in_window(window: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """True if ``now`` (local) falls inside ``{"start": "HH:MM", "end": "HH:MM", "days": [0..6]}``.

    Windows may wrap past midnight (23:00–07:00). For wrapped windows the *start* day decides
    whether the window applies (so "Fri 23:00–07:00" includes Saturday 03:00).
    """
    if not window:
        return True
    now = now or datetime.now()
    start = _parse(window.get("start", "00:00"))
    end = _parse(window.get("end", "00:00"))
    days = window.get("days")
    t = now.time()
    weekday = now.weekday()
    if start == end:
        inside, start_day = True, weekday
    elif start < end:
        inside, start_day = start <= t < end, weekday
    else:
        if t >= start:
            inside, start_day = True, weekday
        elif t < end:
            inside, start_day = True, (weekday - 1) % 7
        else:
            inside, start_day = False, weekday
    if not inside:
        return False
    return days is None or len(days) == 0 or start_day in days


def describe_window(window: dict[str, Any] | None) -> str:
    if not window:
        return "any time"
    return f"{window.get('start')}–{window.get('end')}"
