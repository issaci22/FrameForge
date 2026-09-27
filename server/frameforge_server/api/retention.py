"""Originals kept for a period after conversion: list them, keep them for good, or delete them now."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.sessions import require_user
from ..db.models import Library, RetainedOriginal
from ..db.session import get_db
from ..services import retention

router = APIRouter(prefix="/retention", tags=["retention"], dependencies=[Depends(require_user)])

LIST_LIMIT = 500


@router.get("")
async def list_retained(library_id: int | None = None, include_closed: bool = False, db: AsyncSession = Depends(get_db)) -> list[dict]:
    q = select(RetainedOriginal).order_by(RetainedOriginal.due_at).limit(LIST_LIMIT)
    if library_id is not None:
        q = q.where(RetainedOriginal.library_id == library_id)
    if not include_closed:
        q = q.where(RetainedOriginal.state.in_(retention.OPEN_STATES))
    rows = (await db.execute(q)).scalars().all()
    libs = {lib.id: lib for lib in (await db.execute(select(Library))).scalars()}
    return [retention.entry_view(r, libs.get(r.library_id) if r.library_id is not None else None) for r in rows]


async def _open_entry(db: AsyncSession, entry_id: int) -> RetainedOriginal:
    entry = await db.get(RetainedOriginal, entry_id)
    if entry is None:
        raise HTTPException(404, "Not found")
    if entry.state not in retention.OPEN_STATES:
        raise HTTPException(409, f"This original is already {entry.state}")
    return entry


@router.post("/{entry_id}/keep")
async def keep_forever(entry_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Never delete this original automatically."""
    async with retention.lock:
        entry = await _open_entry(db, entry_id)
        entry.state, entry.reason = "kept", "Kept by you"
        await db.commit()
    library = await db.get(Library, entry.library_id) if entry.library_id is not None else None
    return retention.entry_view(entry, library)


@router.post("/{entry_id}/delete")
async def delete_now(entry_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Delete now instead of waiting. Every safety check still runs; a failed check keeps the file."""
    async with retention.lock:
        entry = await _open_entry(db, entry_id)
        await retention.process_entry(db, entry, manual=True)
        await db.commit()
    library = await db.get(Library, entry.library_id) if entry.library_id is not None else None
    return retention.entry_view(entry, library)


@router.post("/apply-period")
async def apply_period(library_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Recompute pending dates from the library's current period. Shortening it can make originals due now."""
    library = await db.get(Library, library_id)
    if library is None:
        raise HTTPException(404, "Library not found")
    async with retention.lock:
        updated = await retention.apply_period(db, library)
        await db.commit()
    return {"updated": updated}
