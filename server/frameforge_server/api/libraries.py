"""Library CRUD + scan triggers."""

from __future__ import annotations

import asyncio
import os
import posixpath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.protocol import ValidationThresholds

from ..auth.sessions import require_user
from ..db.models import Library, RetainedOriginal
from ..db.session import get_db
from ..services import retention, scanner, stats
from ..services.jobs import DEFAULT_BACKUP_DIR_NAME
from ..services.storage_policy import (
    MAX_RETENTION_DAYS,
    MIN_RETENTION_DAYS,
    KeptOriginalLocation,
    OriginalHandling,
    OutputLocation,
    StoragePolicy,
    apply_to,
    from_legacy,
    storage_of,
)

router = APIRouter(prefix="/libraries", tags=["libraries"], dependencies=[Depends(require_user)])

OutputPolicy = Literal["replace", "backup", "output_dir", "alongside"]


def _norm_path(p: str) -> str:
    p = p.strip().replace("\\", "/")
    if not p.startswith("/"):
        raise ValueError(f"'{p}' must be an absolute path inside the container (e.g. /media/vods)")
    return posixpath.normpath(p)


class LibraryIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    paths: list[str] = Field(min_length=1)
    enabled: bool = True
    automation_enabled: bool = True
    scan_interval_minutes: int = Field(60, ge=5, le=10080)
    exclude_patterns: list[str] = Field(default_factory=list)
    # Legacy single value. Used only when none of the storage fields below are sent.
    output_policy: OutputPolicy = "backup"
    output_location: OutputLocation | None = None
    original_handling: OriginalHandling | None = None
    retention_days: int | None = Field(None, ge=MIN_RETENTION_DAYS, le=MAX_RETENTION_DAYS)
    kept_original_location: KeptOriginalLocation | None = None
    output_path: str | None = None
    backup_path: str | None = None
    validation: ValidationThresholds = Field(default_factory=ValidationThresholds)

    def storage(self) -> StoragePolicy:
        if self.output_location is None and self.original_handling is None and self.kept_original_location is None:
            return from_legacy(self.output_policy, self.output_path)
        handling = self.original_handling or "keep"
        return StoragePolicy(
            output_location=self.output_location or "source_folder",
            original_handling=handling,
            retention_days=self.retention_days if handling == "keep_days" else None,
            kept_original_location=self.kept_original_location or "backup",
        )

    @field_validator("paths")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        out = [_norm_path(p) for p in v if p.strip()]
        if not out:
            raise ValueError("Add at least one folder")
        return out

    @field_validator("output_path", "backup_path")
    @classmethod
    def _opt_path(cls, v: str | None) -> str | None:
        return _norm_path(v) if v and v.strip() else None


def library_view(lib: Library) -> dict:
    return {
        "id": lib.id,
        "name": lib.name,
        "paths": lib.paths,
        "enabled": lib.enabled,
        "automation_enabled": lib.automation_enabled,
        "scan_interval_minutes": lib.scan_interval_minutes,
        "exclude_patterns": lib.exclude_patterns,
        "output_policy": storage_of(lib).legacy,
        "output_location": storage_of(lib).output_location,
        "original_handling": storage_of(lib).original_handling,
        "retention_days": storage_of(lib).retention_days,
        "kept_original_location": storage_of(lib).kept_original_location,
        "output_path": lib.output_path,
        "backup_path": lib.backup_path,
        "effective_backup_path": lib.backup_path or (posixpath.join(lib.paths[0], DEFAULT_BACKUP_DIR_NAME) if lib.paths else None),
        "validation": ValidationThresholds.model_validate(lib.validation or {}).model_dump(),
        "created_at": lib.created_at.isoformat(),
        "last_scan_at": lib.last_scan_at.isoformat() if lib.last_scan_at else None,
        "last_scan_summary": lib.last_scan_summary,
        "scanning": scanner.is_scanning(lib.id),
        "scan_state": scanner.scan_state(lib.id) if scanner.is_scanning(lib.id) else None,
    }


_STORAGE_FIELDS = {"output_policy", "output_location", "original_handling", "retention_days", "kept_original_location", "validation"}


def _plain_fields(body: LibraryIn) -> dict:
    return body.model_dump(exclude=_STORAGE_FIELDS)


async def _retention_summary(db: AsyncSession, lib: Library) -> dict:
    rows = (
        await db.execute(
            select(RetainedOriginal.state, func.count(), func.coalesce(func.sum(RetainedOriginal.original_size), 0), func.min(RetainedOriginal.due_at))
            .where(RetainedOriginal.library_id == lib.id, RetainedOriginal.state.in_(retention.OPEN_STATES))
            .group_by(RetainedOriginal.state)
        )
    ).all()
    out = {"pending": 0, "blocked": 0, "bytes": 0, "next_due": None}
    for state, count, size, due in rows:
        out[state] = count
        out["bytes"] += int(size or 0)
        if state == "pending" and due is not None:
            out["next_due"] = due.isoformat()
    return out


def _check(body: LibraryIn) -> list[str]:
    """Validate folders exist in the container. Returns warnings (non-fatal)."""
    missing = [p for p in body.paths if not os.path.isdir(p)]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Folder not found inside the container: {', '.join(missing)}. Check the volume mounts in your docker-compose.yml.",
        )
    policy = body.storage()
    if policy.output_location == "folder" and not body.output_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an output folder for the 'save to another folder' option")
    if policy.output_location == "folder" and body.output_path in body.paths:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The output folder must be different from the library's own folders")
    if body.original_handling == "keep_days" and not body.retention_days:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose how many days to keep originals")
    warnings = []
    for label, p in (("Output", body.output_path), ("Backup", body.backup_path)):
        if p and not os.path.isdir(p):
            warnings.append(f"{label} folder {p} doesn't exist yet; it will be created when needed")
    return warnings


@router.get("")
async def list_libraries(db: AsyncSession = Depends(get_db)) -> list[dict]:
    libs = (await db.execute(select(Library).order_by(Library.name))).scalars().all()
    out = []
    for lib in libs:
        view = library_view(lib)
        view["stats"] = await stats.library_stats(db, lib)
        view["retention"] = await _retention_summary(db, lib)
        out.append(view)
    return out


@router.post("", status_code=201)
async def create_library(body: LibraryIn, db: AsyncSession = Depends(get_db)) -> dict:
    warnings = _check(body)
    if (await db.execute(select(Library).where(Library.name == body.name))).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "A library with that name already exists")
    lib = Library(**_plain_fields(body), validation=body.validation.model_dump())
    apply_to(lib, body.storage())
    db.add(lib)
    await db.commit()
    asyncio.create_task(scanner.scan_library(lib.id, reason="created"))
    return {**library_view(lib), "warnings": warnings}


@router.get("/{library_id}")
async def get_library(library_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    lib = await db.get(Library, library_id)
    if lib is None:
        raise HTTPException(404, "Library not found")
    return {**library_view(lib), "stats": await stats.library_stats(db, lib), "retention": await _retention_summary(db, lib)}


@router.put("/{library_id}")
async def update_library(library_id: int, body: LibraryIn, db: AsyncSession = Depends(get_db)) -> dict:
    lib = await db.get(Library, library_id)
    if lib is None:
        raise HTTPException(404, "Library not found")
    warnings = _check(body)
    old_days = storage_of(lib).retention_days
    for key, value in _plain_fields(body).items():
        setattr(lib, key, value)
    apply_to(lib, body.storage())
    lib.validation = body.validation.model_dump()
    await retention.on_retention_days_changed(db, lib, old_days)
    await db.commit()
    return {**library_view(lib), "warnings": warnings}


@router.delete("/{library_id}")
async def delete_library(library_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Removes the library from FrameForge only. Files on disk are never touched."""
    lib = await db.get(Library, library_id)
    if lib is None:
        raise HTTPException(404, "Library not found")
    await db.delete(lib)
    await db.commit()
    return {"ok": True}


@router.post("/{library_id}/scan")
async def scan(library_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    lib = await db.get(Library, library_id)
    if lib is None:
        raise HTTPException(404, "Library not found")
    if scanner.is_scanning(library_id):
        return {"status": "already_running"}
    asyncio.create_task(scanner.scan_library(library_id, reason="manual"))
    return {"status": "started"}


@router.post("/{library_id}/evaluate")
async def evaluate(library_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Re-run the rules without rescanning the disk."""
    created = await scanner.evaluate_library(db, library_id)
    return {"jobs_created": created}
