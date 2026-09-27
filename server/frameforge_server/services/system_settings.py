"""Global, UI-editable settings stored in the ``system_settings`` table."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import SystemSetting
from ..db.session import sessionmaker

_KEY = "general"


class TimeWindow(BaseModel):
    start: str = Field("23:00", pattern=r"^\d{2}:\d{2}$")
    end: str = Field("07:00", pattern=r"^\d{2}:\d{2}$")
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6], description="0 = Monday")


class QuietHours(BaseModel):
    """Background work only runs inside this window."""

    enabled: bool = False
    window: TimeWindow = Field(default_factory=TimeWindow)
    applies_to: Literal["background", "low_and_below"] = "background"


class GeneralSettings(BaseModel):
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    max_attempts: int = Field(2, ge=1, le=10, description="Automatic retries for transient failures")
    probe_concurrency: int = Field(4, ge=1, le=32)
    job_history_days: int = Field(180, ge=7, le=3650)
    public_url: str | None = Field(None, description="URL nodes use to reach this server")


_cache: GeneralSettings | None = None


async def load_settings(db: AsyncSession | None = None) -> GeneralSettings:
    global _cache
    if _cache is not None:
        return _cache
    if db is None:
        async with sessionmaker()() as s:
            row = await s.get(SystemSetting, _KEY)
    else:
        row = await db.get(SystemSetting, _KEY)
    _cache = GeneralSettings.model_validate(row.value) if row else GeneralSettings()
    return _cache


async def save_settings(db: AsyncSession, settings: GeneralSettings) -> GeneralSettings:
    global _cache
    row = await db.get(SystemSetting, _KEY)
    data = settings.model_dump(mode="json")
    if row is None:
        db.add(SystemSetting(key=_KEY, value=data))
    else:
        row.value = data
    await db.commit()
    _cache = settings
    return settings
