"""Built-in profiles: goal-named starting points, seeded on first start and upgraded once.

The lineup is organized by goal (compatibility, balance, space, quality) plus a few special-purpose
profiles. Earlier versions shipped use-case profiles (YouTube Archive, Storage Saver, Long-Term
Archive). Those are upgraded in place exactly once (lineup marker), and only when the user never
edited them: same name, same settings as shipped. Rules keep pointing at the same profile id.
Edited ones are left untouched and the new profile is added next to them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.profile import ProfileSpec

from ..db.models import Profile, SystemSetting

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Builtin:
    key: str
    name: str
    description: str
    spec: dict[str, Any]
    group: str  # "goal" (starting points) | "special"
    tagline: str  # one line for the starting-point cards


BUILTINS: list[Builtin] = [
    Builtin(
        "max_compat",
        "Maximum Compatibility",
        "H.264 + AAC in MP4. Plays on anything: old TVs, every browser, every editor. The largest of the goal profiles.",
        {"container": "mp4", "video_codec": "h264", "quality": 75, "speed": "balanced", "audio_mode": "transcode", "audio_codec": "aac", "audio_bitrate_kbps": 160},
        "goal",
        "Plays everywhere",
    ),
    Builtin(
        "balanced",
        "Balanced",
        "H.265 in MP4 at good quality: roughly half the size of typical H.264 recordings and hard to tell apart. Keeps audio "
        "untouched when it plays widely in MP4, otherwise converts it to AAC.",
        {
            "container": "mp4",
            "video_codec": "hevc",
            "quality": 68,
            "speed": "balanced",
            "audio_mode": "copy_compatible",
            "audio_copy_scope": "widely_playable",
            "audio_codec": "aac",
            "audio_bitrate_kbps": 160,
        },
        "goal",
        "Good quality, about half the size",
    ),
    Builtin(
        "space_saver",
        "Space Saver",
        "H.265 in MP4 tuned for size, with a slower encoder preset to squeeze out more. For footage you want to keep but will rarely watch.",
        {"container": "mp4", "video_codec": "hevc", "quality": 52, "speed": "quality", "audio_mode": "transcode", "audio_codec": "aac", "audio_bitrate_kbps": 128},
        "goal",
        "Much smaller, some detail lost",
    ),
    Builtin(
        "smallest_av1",
        "Smallest Files (AV1)",
        "AV1 + Opus in MP4. The smallest files at good quality. Fast on RTX 40-series, Intel Arc and RX 7000 GPUs; slow on the CPU.",
        {"container": "mp4", "video_codec": "av1", "quality": 62, "speed": "quality", "audio_mode": "transcode", "audio_codec": "opus", "audio_bitrate_kbps": 96},
        "goal",
        "Smallest files, needs a recent GPU",
    ),
    Builtin(
        "high_quality",
        "High Quality",
        "H.265 in MKV at high quality with a slower preset. Keeps every audio track, subtitle and attachment as-is. For footage you may re-edit.",
        {"container": "mkv", "video_codec": "hevc", "quality": 84, "speed": "quality", "audio_mode": "copy_compatible", "audio_codec": "aac", "audio_bitrate_kbps": 256},
        "goal",
        "Near the original, keeps everything",
    ),
    Builtin(
        "youtube_upload",
        "YouTube Upload",
        "H.264 + AAC in MP4, following YouTube's upload recommendations. Use it to make upload-ready copies, not archives.",
        {"container": "mp4", "video_codec": "h264", "quality": 85, "speed": "quality", "audio_mode": "transcode", "audio_codec": "aac", "audio_bitrate_kbps": 384, "faststart": True},
        "special",
        "Upload-ready copies",
    ),
    Builtin(
        "stream_vod",
        "Stream VOD Archive",
        "For long Twitch/YouTube livestream recordings: 1080p cap, 60 fps cap, H.265, Opus audio.",
        {
            "container": "mkv",
            "video_codec": "hevc",
            "quality": 60,
            "speed": "balanced",
            "max_resolution": 1080,
            "max_fps": 60,
            "audio_mode": "transcode",
            "audio_codec": "opus",
            "audio_bitrate_kbps": 128,
        },
        "special",
        "Long livestream recordings",
    ),
    Builtin(
        "obs_remux",
        "OBS Remux to MP4",
        "No re-encoding. Moves OBS MKV recordings into MP4 so editors accept them. Fast and lossless.",
        {"container": "mp4", "video_codec": "copy", "audio_mode": "copy_compatible", "audio_codec": "aac", "faststart": True},
        "special",
        "Lossless container change",
    ),
    Builtin(
        "editing_proxy",
        "Editing Proxy (720p)",
        "Lightweight 720p H.264 proxies for smooth timeline editing. Pair with a library that writes to an output folder.",
        {"container": "mp4", "video_codec": "h264", "quality": 45, "speed": "fast", "max_resolution": 720, "audio_mode": "transcode", "audio_codec": "aac", "audio_bitrate_kbps": 128},
        "special",
        "Smooth editing",
    ),
]
BUILTIN_BY_KEY = {b.key: b for b in BUILTINS}
BUILTIN_ORDER = {b.key: i for i, b in enumerate(BUILTINS)}

# The first lineup, frozen exactly as it shipped (name, spec arguments). Used only to recognize unedited copies.
LEGACY_SEEDS_V1: dict[str, tuple[str, dict[str, Any]]] = {
    "youtube_archive": ("YouTube Archive", {"container": "mkv", "video_codec": "hevc", "quality": 72, "speed": "balanced", "audio_mode": "copy_compatible", "audio_codec": "aac"}),
    "long_term_av1": (
        "Long-Term Archive (AV1)",
        {"container": "mkv", "video_codec": "av1", "quality": 70, "speed": "quality", "audio_mode": "transcode", "audio_codec": "opus", "audio_bitrate_kbps": 160},
    ),
    "storage_saver": (
        "Storage Saver",
        {"container": "mkv", "video_codec": "hevc", "quality": 55, "speed": "balanced", "audio_mode": "transcode", "audio_codec": "opus", "audio_bitrate_kbps": 128},
    ),
}
SUCCESSORS: dict[str, str] = {"youtube_archive": "balanced", "storage_saver": "space_saver", "long_term_av1": "smallest_av1"}
LEGACY_KEYS = frozenset(SUCCESSORS)

_SEEDED_KEY = "seeded_profiles"
_LINEUP_KEY = "profile_lineup"
_NOTICES_KEY = "profile_notices"
LINEUP_VERSION = 2


def spec_of(builtin: Builtin) -> dict[str, Any]:
    return ProfileSpec.model_validate(builtin.spec).model_dump(mode="json")


def _norm(doc: dict[str, Any]) -> dict[str, Any]:
    return ProfileSpec.model_validate(doc).model_dump(mode="json", exclude={"version"})


async def _setting(db: AsyncSession, key: str) -> SystemSetting | None:
    return await db.get(SystemSetting, key)


async def _set(db: AsyncSession, key: str, value: Any) -> None:
    row = await _setting(db, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value


async def _upgrade_lineup(db: AsyncSession, seeded: list[str], profiles: list[Profile]) -> list[str]:
    """Rename/re-spec unedited first-lineup built-ins in place. Returns notices for the user."""
    notices: list[str] = []
    names = {p.name for p in profiles}
    by_key = {p.builtin_key: p for p in profiles if p.builtin_key}
    for old_key, new_key in SUCCESSORS.items():
        new = BUILTIN_BY_KEY[new_key]
        row = by_key.get(old_key)
        if row is None:
            if old_key in seeded:
                seeded.append(new_key)  # the user deleted it: don't bring its successor back uninvited
            continue
        old_name, old_spec = LEGACY_SEEDS_V1[old_key]
        unedited = row.name == old_name and _norm(row.spec or {}) == _norm(old_spec)
        if not unedited:
            log.info("Built-in profile %r was edited; leaving it and adding %r next to it", row.name, new.name)
            continue
        if new.name in names:
            log.info("Can't rename built-in profile %r to %r: that name is taken", row.name, new.name)
            continue
        names.discard(row.name)
        names.add(new.name)
        row.name, row.description, row.spec, row.builtin_key = new.name, new.description, spec_of(new), new_key
        seeded.append(new_key)
        notices.append(f"The built-in profile “{old_name}” is now “{new.name}”: {new.tagline.lower()}. Rules that used it now use “{new.name}”.")
    return notices


async def seed_profiles(db: AsyncSession) -> None:
    """Add built-ins that were never seeded before (built-ins a user deleted stay deleted), upgrading the old lineup once."""
    marker = await _setting(db, _SEEDED_KEY)
    seeded: list[str] = list(marker.value) if marker else []
    profiles = list((await db.execute(select(Profile))).scalars())

    lineup = await _setting(db, _LINEUP_KEY)
    if (lineup.value if lineup else 1) < LINEUP_VERSION:
        notices = await _upgrade_lineup(db, seeded, profiles) if seeded else []
        await _set(db, _LINEUP_KEY, LINEUP_VERSION)
        if notices:
            existing = await _setting(db, _NOTICES_KEY)
            await _set(db, _NOTICES_KEY, list(existing.value if existing else []) + notices)

    names = {p.name for p in profiles}
    added = 0
    for b in BUILTINS:
        if b.key in seeded:
            continue
        seeded.append(b.key)
        if b.name in names:
            continue
        db.add(Profile(name=b.name, description=b.description, builtin_key=b.key, spec=spec_of(b)))
        names.add(b.name)
        added += 1
    await _set(db, _SEEDED_KEY, seeded)
    await db.commit()
    if added:
        log.info("Seeded %d built-in profiles", added)


async def notices(db: AsyncSession) -> list[str]:
    row = await _setting(db, _NOTICES_KEY)
    return list(row.value) if row and row.value else []


async def dismiss_notices(db: AsyncSession) -> None:
    await _set(db, _NOTICES_KEY, [])
    await db.commit()


def starting_points() -> list[dict[str, Any]]:
    return [{"key": b.key, "name": b.name, "description": b.description, "tagline": b.tagline, "group": b.group, "spec": spec_of(b)} for b in BUILTINS]
