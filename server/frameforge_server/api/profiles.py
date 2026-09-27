"""Transcoding profiles, the editor's format/hardware knowledge, and the command preview."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.codecs import AUDIO_LABELS, BACKEND_LABELS, ENCODERS, Backend, VideoCodec
from frameforge_shared.compat import check_spec, has_errors, options_document
from frameforge_shared.encoders import select_encoder
from frameforge_shared.ffmpeg_builder import BuildError, build_command
from frameforge_shared.media import AudioStream, MediaInfo, VideoStream
from frameforge_shared.profile import ProfileSpec
from frameforge_shared.protocol import EncoderChoice
from frameforge_shared.quality import QUALITY_TIERS, map_quality, quality_tier

from ..auth.sessions import require_user
from ..db.models import Job, Profile, Rule
from ..db.session import get_db
from ..services import presets
from ..services.node_manager import manager
from ..services.previews import previews

router = APIRouter(prefix="/profiles", tags=["profiles"], dependencies=[Depends(require_user)])

HISTORY_MIN_JOBS = 3  # below this, an average says more about the files than the profile


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    based_on: str | None = Field(None, max_length=64)
    spec: ProfileSpec


def _validate_spec(spec: ProfileSpec) -> None:
    issues = check_spec(spec)
    if has_errors(issues):
        raise HTTPException(422, " ".join(i.message for i in issues if i.level == "error"))


def profile_view(p: Profile, usage: int = 0, history: dict | None = None) -> dict:
    spec = ProfileSpec.model_validate(p.spec)
    builtin = presets.BUILTIN_BY_KEY.get(p.builtin_key or "")
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "builtin": p.builtin_key is not None,
        "builtin_key": p.builtin_key,
        "builtin_group": builtin.group if builtin else ("legacy" if p.builtin_key in presets.LEGACY_KEYS else None),
        "based_on": p.based_on,
        "spec": spec.model_dump(mode="json"),
        "summary": _summary(spec),
        "rule_count": usage,
        "history": history,
        "updated_at": p.updated_at.isoformat(),
    }


def _summary(spec: ProfileSpec) -> str:
    parts = [spec.container.value.upper(), spec.target_label()]
    if not spec.is_remux:
        parts.append(f"quality {spec.quality} ({quality_tier(spec.quality).label.lower()})")
        if spec.max_resolution:
            parts.append(f"≤{spec.max_resolution}p")
        if spec.max_fps:
            parts.append(f"≤{spec.max_fps:g} fps")
    audio = AUDIO_LABELS.get(spec.audio_codec, spec.audio_codec.upper())
    kbps = "" if spec.audio_codec == "flac" else f" {spec.audio_bitrate_kbps}k"
    parts.append({"copy": "audio copied", "copy_compatible": "audio copied when possible", "transcode": f"{audio}{kbps}"}[spec.audio_mode])
    return " · ".join(parts)


async def _history(db: AsyncSession, p: Profile) -> dict | None:
    """What this profile actually did to real files since it was last changed. Never a prediction."""
    since: datetime = p.updated_at
    row = (
        await db.execute(
            select(func.count(), func.sum(Job.source_size), func.sum(Job.output_size)).where(
                Job.profile_id == p.id, Job.state == "completed", Job.finished_at >= since, Job.source_size > 0, Job.output_size > 0
            )
        )
    ).one()
    count, source, output = row
    if not count or count < HISTORY_MIN_JOBS or not source:
        return {"jobs": int(count or 0), "ratio": None}
    return {"jobs": int(count), "ratio": float(output) / float(source)}


def _sort_key(p: Profile) -> tuple:
    order = presets.BUILTIN_ORDER.get(p.builtin_key or "")
    return (0 if order is not None else 1 if p.builtin_key else 2, order or 0, p.name.lower())


@router.get("")
async def list_profiles(db: AsyncSession = Depends(get_db)) -> list[dict]:
    usage = dict((await db.execute(select(Rule.profile_id, func.count()).group_by(Rule.profile_id))).all())
    rows = sorted((await db.execute(select(Profile))).scalars().all(), key=_sort_key)
    return [profile_view(p, usage.get(p.id, 0), await _history(db, p)) for p in rows]


@router.get("/options")
async def options(db: AsyncSession = Depends(get_db)) -> dict:
    """Everything the editor needs to explain choices: formats, starting points, quality bands, hardware."""
    return {
        **options_document(),
        "starting_points": presets.starting_points(),
        "quality_tiers": [{"key": t.key, "label": t.label, "description": t.description, "min": t.min_slider} for t in QUALITY_TIERS],
        "hardware": manager.hardware_matrix(),
        "online_nodes": len(manager.connections),
        "backend_labels": {b.value: label for b, label in BACKEND_LABELS.items()},
        "notices": await presets.notices(db),
    }


@router.post("/notices/dismiss")
async def dismiss_notices(db: AsyncSession = Depends(get_db)) -> dict:
    await presets.dismiss_notices(db)
    return {"ok": True}


@router.post("", status_code=201)
async def create_profile(body: ProfileIn, db: AsyncSession = Depends(get_db)) -> dict:
    _validate_spec(body.spec)
    if (await db.execute(select(Profile).where(Profile.name == body.name))).scalar_one_or_none():
        raise HTTPException(409, "A profile with that name already exists")
    p = Profile(name=body.name, description=body.description, based_on=body.based_on, spec=body.spec.model_dump(mode="json"))
    db.add(p)
    await db.commit()
    return profile_view(p)


@router.get("/{profile_id}")
async def get_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    p = await db.get(Profile, profile_id)
    if p is None:
        raise HTTPException(404, "Profile not found")
    return profile_view(p, history=await _history(db, p))


@router.put("/{profile_id}")
async def update_profile(profile_id: int, body: ProfileIn, db: AsyncSession = Depends(get_db)) -> dict:
    p = await db.get(Profile, profile_id)
    if p is None:
        raise HTTPException(404, "Profile not found")
    _validate_spec(body.spec)
    clash = (await db.execute(select(Profile).where(Profile.name == body.name, Profile.id != profile_id))).scalar_one_or_none()
    if clash:
        raise HTTPException(409, "A profile with that name already exists")
    p.name, p.description, p.spec = body.name, body.description, body.spec.model_dump(mode="json")
    if body.based_on is not None:
        p.based_on = body.based_on
    await db.commit()
    return profile_view(p)


@router.delete("/{profile_id}")
async def delete_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    p = await db.get(Profile, profile_id)
    if p is None:
        raise HTTPException(404, "Profile not found")
    used = (await db.execute(select(Rule.name).where(Rule.profile_id == profile_id))).scalars().all()
    if used:
        raise HTTPException(409, f"Used by rule(s): {', '.join(used)}. Change those rules first.")
    queued = (await db.execute(select(func.count()).select_from(Job).where(Job.profile_id == profile_id, Job.state == "queued"))).scalar_one()
    if queued:
        raise HTTPException(409, f"{queued} queued job(s) use this profile")
    await db.delete(p)
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Preview (a dry run: nothing is encoded)
# ---------------------------------------------------------------------------

_SAMPLE = MediaInfo(
    format_name="matroska,webm",
    container="mkv",
    duration=3600.0,
    size=40 * 1024**3,
    bitrate=90_000_000,
    video=VideoStream(index=0, codec="h264", width=3840, height=2160, fps=60.0, pix_fmt="yuv420p", bit_depth=8),
    audio=[AudioStream(index=1, codec="aac", channels=2), AudioStream(index=2, codec="aac", channels=2, title="Mic")],
)


class PreviewRequest(BaseModel):
    spec: dict


@router.post("/preview")
async def preview(body: PreviewRequest) -> dict:
    """Explain a profile: advice, native quality per encoder, what each node would use, example commands."""
    try:
        spec = ProfileSpec.model_validate(body.spec)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    available = manager.available_backends() if manager.connections else None
    tier = quality_tier(spec.quality)
    result: dict = {
        "quality": [],
        "commands": [],
        "engine_available": spec.engine == "ffmpeg",
        "issues": [i.model_dump() for i in check_spec(spec, available)],
        "tier": {"key": tier.key, "label": tier.label, "description": tier.description},
        "compression_preview": previews.availability(spec),
    }
    if spec.engine != "ffmpeg":
        result["message"] = "The HandBrake engine isn't available in this version of FrameForge."
        return result
    override = spec.constant_quality if spec.rate_control == "constant_quality" else None
    if not spec.is_remux:
        codec = VideoCodec(spec.video_codec)
        for backend in Backend:
            q = map_quality(spec.quality, codec, backend, override)
            result["quality"].append({"backend": backend.value, "label": BACKEND_LABELS[backend], "encoder": ENCODERS[codec][backend], "value": q.value, "param": q.param, "range": [q.min_value, q.max_value]})
    backends = [Backend.CPU] if spec.is_remux else list(Backend)
    for backend in backends:
        choice = (
            EncoderChoice(codec="copy", backend="cpu", encoder="copy")
            if spec.is_remux
            else EncoderChoice(codec=spec.video_codec.value, backend=backend.value, encoder=ENCODERS[VideoCodec(spec.video_codec)][backend], device="/dev/dri/renderD128")
        )
        try:
            built = build_command(_SAMPLE, spec, choice, "/media/input.mkv", "/media/.frameforge-tmp/output" + (".mp4" if spec.container.value == "mp4" else ".mkv"), "job=0;profile=0;v=1")
        except BuildError as exc:
            result["commands"].append({"backend": backend.value, "error": str(exc)})
            continue
        result["commands"].append({"backend": backend.value, "label": BACKEND_LABELS[backend], "command": built.command_line(), "pipeline": built.pipeline, "notes": built.notes})
    # Which online nodes could run it right now, with which encoder and native quality value?
    result["nodes"] = []
    for conn in manager.connections.values():
        sel = select_encoder(spec, conn.caps, _SAMPLE)
        native = None
        if sel.choice and not spec.is_remux:
            q = map_quality(spec.quality, VideoCodec(sel.choice.codec), Backend(sel.choice.backend), override)
            native = q.label if spec.rate_control != "bitrate" else f"{spec.bitrate_kbps} kb/s"
        result["nodes"].append(
            {"node_id": conn.node_id, "name": conn.name, "encoder": sel.choice.encoder if sel.choice else None, "backend": sel.choice.backend if sel.choice else None, "native": native, "reason": sel.reason}
        )
    result["sample"] = "Example source: 4K60 H.264 MKV, 1 h, two AAC tracks"
    return result
