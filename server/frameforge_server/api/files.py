"""Browse discovered media, explain rule decisions, queue files manually."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.codecs import codec_label
from frameforge_shared.media import MediaInfo
from frameforge_shared.probe import ProbeError, probe_file

from ..auth.sessions import require_user
from ..db.models import Job, MediaFile, MediaMetadata, Profile, Rule
from ..db.session import get_db
from ..services import jobs as job_service
from ..services import retention
from ..services.node_manager import manager
from ..services.rules.engine import build_context, decide
from ..services.scanner import metadata_from_info
from ..services.scheduler import scheduler

router = APIRouter(prefix="/files", tags=["files"], dependencies=[Depends(require_user)])

SORTS = {
    "name": MediaFile.filename,
    "size": MediaFile.size,
    "modified": MediaFile.mtime,
    "status": MediaFile.status,
}


def file_view(f: MediaFile) -> dict:
    m = f.meta
    info = MediaInfo.model_validate(m.info) if m and m.info else None
    return {
        "id": f.id,
        "library_id": f.library_id,
        "path": f.path,
        "relative_path": f.relative_path,
        "filename": f.filename,
        "extension": f.extension,
        "size": f.size,
        "original_size": f.original_size,
        "mtime": f.mtime.isoformat(),
        "status": f.status,
        "ignored": f.ignored,
        "decision": f.decision,
        "probe_error": f.probe_error,
        "processed_profile_id": f.processed_profile_id,
        "processed_at": f.processed_at.isoformat() if f.processed_at else None,
        "last_job_id": f.last_job_id,
        "media": None
        if m is None
        else {
            "container": m.container,
            "duration": m.duration,
            "bitrate": m.bitrate,
            "video_codec": m.video_codec,
            "video_codec_label": codec_label(m.video_codec),
            "width": m.width,
            "height": m.height,
            "fps": m.fps,
            "bit_depth": m.bit_depth,
            "hdr_format": m.hdr_format,
            "audio_codecs": m.audio_codecs,
            "audio_count": m.audio_count,
            "subtitle_count": m.subtitle_count,
            "chapter_count": m.chapter_count,
            "creation_time": m.creation_time.isoformat() if m.creation_time else None,
            "resolution_label": info.resolution_label() if info else None,
        },
    }


@router.get("")
async def list_files(
    library_id: int | None = None,
    status: str | None = None,
    codec: str | None = None,
    q: str | None = None,
    sort: str = "modified",
    order: Literal["asc", "desc"] = "desc",
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(MediaFile)
    if library_id is not None:
        stmt = stmt.where(MediaFile.library_id == library_id)
    if status:
        stmt = stmt.where(MediaFile.status.in_(status.split(",")))
    if codec:
        stmt = stmt.join(MediaMetadata, MediaMetadata.file_id == MediaFile.id).where(MediaMetadata.video_codec == codec)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(MediaFile.filename.ilike(like), MediaFile.relative_path.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    col = SORTS.get(sort, MediaFile.mtime)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc(), MediaFile.id.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {"total": total, "items": [file_view(f) for f in rows]}


async def _get(db: AsyncSession, file_id: int) -> MediaFile:
    f = await db.get(MediaFile, file_id)
    if f is None:
        raise HTTPException(404, "File not found")
    return f


@router.get("/{file_id}")
async def get_file(file_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    f = await _get(db, file_id)
    rules = list((await db.execute(select(Rule))).scalars())
    active = await job_service.active_job_for_file(db, f.id)
    failed = set((await db.execute(select(Job.profile_id).where(Job.file_id == f.id, Job.state == "failed"))).scalars())
    decision = decide(f, rules, build_context(f, manager.hardware_snapshot()), has_active_job=active is not None, failed_profile_ids=failed, with_traces=True)
    jobs = (await db.execute(select(Job).where(Job.file_id == f.id).order_by(Job.id.desc()).limit(20))).scalars().all()
    return {
        **file_view(f),
        "info": f.meta.info if f.meta else None,
        "evaluation": {
            "action": decision.action,
            "reason": decision.reason,
            "rule_id": decision.rule.id if decision.rule else None,
            "profile_id": decision.profile.id if decision.profile else None,
            "rules": decision.rule_traces,
        },
        "jobs": [job_service.job_summary(j) for j in jobs],
    }


class QueueRequest(BaseModel):
    profile_id: int
    priority: int = Field(3, ge=0, le=4)


@router.post("/{file_id}/queue", status_code=201)
async def queue_file(file_id: int, body: QueueRequest, db: AsyncSession = Depends(get_db)) -> dict:
    f = await _get(db, file_id)
    if f.status in ("missing", "new", "error"):
        raise HTTPException(400, "This file can't be processed until it has been analyzed successfully")
    if await job_service.active_job_for_file(db, f.id):
        raise HTTPException(409, "This file already has a job in the queue")
    if retention.is_being_deleted(f.path):
        raise HTTPException(409, "This original is being deleted by its retention policy right now")
    profile = await db.get(Profile, body.profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    job = await job_service.create_job(db, f, profile, priority=body.priority, manual=True)
    await db.commit()
    job_service.publish_job(job, "job.created")
    scheduler.wake()
    return job_service.job_summary(job)


class IgnoreRequest(BaseModel):
    ignored: bool


@router.post("/{file_id}/ignore")
async def ignore_file(file_id: int, body: IgnoreRequest, db: AsyncSession = Depends(get_db)) -> dict:
    f = await _get(db, file_id)
    f.ignored = body.ignored
    f.decision = "Ignored by you" if body.ignored else None
    await db.commit()
    return file_view(f)


@router.post("/{file_id}/reprobe")
async def reprobe(file_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    f = await _get(db, file_id)
    try:
        info = await probe_file(f.path)
    except ProbeError as exc:
        f.status = "error"
        f.probe_error = str(exc)
        await db.commit()
        raise HTTPException(422, f"Couldn't analyze the file: {exc}") from exc
    meta = metadata_from_info(f.id, info)
    if f.meta is None:
        f.meta = meta
    else:
        for col in MediaMetadata.__table__.columns.keys():
            if col != "file_id":
                setattr(f.meta, col, getattr(meta, col))
    f.probe_error = None
    if f.status in ("error", "new"):
        f.status = "ready"
    await db.commit()
    return file_view(f)
