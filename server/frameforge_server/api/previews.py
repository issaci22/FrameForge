"""Compression previews: encode a few short samples of a real file and compare frames."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.compat import check_spec, has_errors
from frameforge_shared.media import MediaInfo
from frameforge_shared.profile import ProfileSpec

from ..auth.sessions import require_user
from ..db.models import MediaFile
from ..db.session import get_db
from ..services.previews import MAX_POSITIONS, PreviewUnavailable, previews

router = APIRouter(prefix="/previews", tags=["previews"], dependencies=[Depends(require_user)])


class PreviewIn(BaseModel):
    file_id: int
    spec: dict
    positions: list[float] | None = Field(None, max_length=MAX_POSITIONS, description="Fractions of the duration (0–1)")
    node_id: int | None = None


@router.post("", status_code=202)
async def start_preview(body: PreviewIn, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        spec = ProfileSpec.model_validate(body.spec)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0]["msg"]) from exc
    issues = check_spec(spec)
    if has_errors(issues):
        raise HTTPException(422, next(i.message for i in issues if i.level == "error"))
    f = await db.get(MediaFile, body.file_id)
    if f is None:
        raise HTTPException(404, "File not found")
    if not f.meta or not f.meta.info:
        raise HTTPException(400, "This file hasn't been analyzed yet")
    source = MediaInfo.model_validate(f.meta.info)
    try:
        p = await previews.start(
            file_id=f.id, source_path=f.path, filename=f.filename, source=source, spec=spec, node_id=body.node_id, positions=body.positions
        )
    except PreviewUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    return previews.view(p)


@router.get("/{preview_id}")
async def get_preview(preview_id: str) -> dict:
    p = previews.items.get(preview_id)
    if p is None:
        raise HTTPException(404, "Preview not found (previews are kept for a few hours)")
    return previews.view(p)


@router.delete("/{preview_id}")
async def cancel_preview(preview_id: str) -> dict:
    p = await previews.cancel(preview_id)
    if p is None:
        raise HTTPException(404, "Preview not found")
    return previews.view(p)


@router.get("/{preview_id}/frames/{index}/{kind}.png")
async def preview_frame(preview_id: str, index: int, kind: Literal["original", "encoded"]) -> FileResponse:
    path = previews.frame_path(preview_id, index, kind)
    if path is None:
        raise HTTPException(404, "Frame not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
