"""Rules: CRUD, ordering, live preview, and the Aging Policy helper."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.sessions import require_user
from ..db.models import Job, Library, MediaFile, Profile, Rule, SystemSetting
from ..db.session import get_db
from ..services.node_manager import manager
from ..services.rules.conditions import ConditionError, ConditionGroup, field_catalog, validate_tree
from ..services.rules.engine import PRIORITY_NAMES, build_context, decide, evaluate_tree, make_condition

router = APIRouter(prefix="/rules", tags=["rules"], dependencies=[Depends(require_user)])


class ScheduleIn(BaseModel):
    window: dict | None = None  # {"start": "23:00", "end": "07:00", "days": [...]}


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    library_id: int | None = None
    enabled: bool = True
    conditions: ConditionGroup = Field(default_factory=ConditionGroup)
    action: Literal["transcode", "skip"] = "transcode"
    profile_id: int | None = None
    priority: int = Field(2, ge=0, le=4)
    schedule: ScheduleIn = Field(default_factory=ScheduleIn)
    skip_if_target_codec: bool = True

    @model_validator(mode="after")
    def _check(self) -> RuleIn:
        if self.action == "transcode" and self.profile_id is None:
            raise ValueError("Choose a profile for this rule")
        try:
            validate_tree(self.conditions)
        except ConditionError as exc:
            raise ValueError(str(exc)) from exc
        return self


def rule_view(r: Rule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "library_id": r.library_id,
        "position": r.position,
        "enabled": r.enabled,
        "conditions": r.conditions or ConditionGroup().model_dump(),
        "action": r.action,
        "profile_id": r.profile_id,
        "profile_name": r.profile.name if r.profile else None,
        "priority": r.priority,
        "priority_name": PRIORITY_NAMES.get(r.priority, str(r.priority)),
        "schedule": r.schedule or {},
        "skip_if_target_codec": r.skip_if_target_codec,
        "policy_group": r.policy_group,
        "updated_at": r.updated_at.isoformat(),
    }


async def _check_refs(db: AsyncSession, body: RuleIn) -> None:
    if body.library_id is not None and await db.get(Library, body.library_id) is None:
        raise HTTPException(404, "Library not found")
    if body.profile_id is not None and await db.get(Profile, body.profile_id) is None:
        raise HTTPException(404, "Profile not found")


@router.get("/fields")
async def fields() -> dict:
    return {"fields": field_catalog(), "priorities": [{"value": k, "label": v} for k, v in PRIORITY_NAMES.items()]}


@router.get("")
async def list_rules(library_id: int | None = None, db: AsyncSession = Depends(get_db)) -> list[dict]:
    stmt = select(Rule).order_by(Rule.position, Rule.id)
    if library_id is not None:
        stmt = stmt.where((Rule.library_id == library_id) | Rule.library_id.is_(None))
    return [rule_view(r) for r in (await db.execute(stmt)).scalars()]


@router.post("", status_code=201)
async def create_rule(body: RuleIn, db: AsyncSession = Depends(get_db)) -> dict:
    await _check_refs(db, body)
    pos = (await db.execute(select(func.coalesce(func.max(Rule.position), -1)))).scalar_one() + 1
    r = Rule(**body.model_dump(exclude={"conditions", "schedule"}), conditions=body.conditions.model_dump(), schedule=body.schedule.model_dump(exclude_none=True), position=pos)
    db.add(r)
    await db.commit()
    await db.refresh(r, ["profile"])
    return rule_view(r)


@router.put("/{rule_id}")
async def update_rule(rule_id: int, body: RuleIn, db: AsyncSession = Depends(get_db)) -> dict:
    r = await db.get(Rule, rule_id)
    if r is None:
        raise HTTPException(404, "Rule not found")
    await _check_refs(db, body)
    for key, value in body.model_dump(exclude={"conditions", "schedule"}).items():
        setattr(r, key, value)
    r.conditions = body.conditions.model_dump()
    r.schedule = body.schedule.model_dump(exclude_none=True)
    r.policy_group = None  # hand-edited rules leave their aging policy
    await db.commit()
    await db.refresh(r, ["profile"])
    return rule_view(r)


@router.delete("/{rule_id}")
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    r = await db.get(Rule, rule_id)
    if r is None:
        raise HTTPException(404, "Rule not found")
    await db.delete(r)
    await db.commit()
    return {"ok": True}


class ReorderRequest(BaseModel):
    ids: list[int]


@router.post("/reorder")
async def reorder(body: ReorderRequest, db: AsyncSession = Depends(get_db)) -> list[dict]:
    rules = {r.id: r for r in (await db.execute(select(Rule))).scalars()}
    for pos, rid in enumerate(body.ids):
        if rid in rules:
            rules[rid].position = pos
    await db.commit()
    return [rule_view(r) for r in sorted(rules.values(), key=lambda r: (r.position, r.id))]


class PreviewRequest(BaseModel):
    conditions: ConditionGroup
    library_id: int | None = None
    limit: int = Field(25, ge=1, le=200)


@router.post("/preview")
async def preview(body: PreviewRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Which files would these conditions match right now?"""
    try:
        validate_tree(body.conditions)
    except ConditionError as exc:
        raise HTTPException(422, str(exc)) from exc
    stmt = select(MediaFile).where(MediaFile.status.not_in(("missing", "error", "new")))
    if body.library_id is not None:
        stmt = stmt.where(MediaFile.library_id == body.library_id)
    hw = manager.hardware_snapshot()
    sample: list[dict] = []
    count = total = total_bytes = 0
    for f in (await db.execute(stmt)).scalars():
        total += 1
        if not evaluate_tree(body.conditions, build_context(f, hw)):
            continue
        count += 1
        total_bytes += f.size
        if len(sample) < body.limit:
            sample.append(
                {"id": f.id, "filename": f.filename, "relative_path": f.relative_path, "size": f.size, "mtime": f.mtime.isoformat(), "video_codec": f.meta.video_codec if f.meta else None, "status": f.status}
            )
    return {"count": count, "total_files": total, "total_bytes": total_bytes, "sample": sample}


@router.get("/dry-run")
async def dry_run(library_id: int, limit: int = Query(200, ge=1, le=2000), db: AsyncSession = Depends(get_db)) -> dict:
    """Run the full rule set against a library without creating jobs."""
    rules = list((await db.execute(select(Rule))).scalars())
    files = (await db.execute(select(MediaFile).where(MediaFile.library_id == library_id).limit(limit))).scalars().all()
    hw = manager.hardware_snapshot()
    summary: dict[str, int] = {}
    items = []
    for f in files:
        active = (await db.execute(select(Job.id).where(Job.file_id == f.id, Job.state.not_in(("completed", "failed", "cancelled"))).limit(1))).first() is not None
        d = decide(f, rules, build_context(f, hw), has_active_job=active)
        summary[d.action] = summary.get(d.action, 0) + 1
        items.append({"id": f.id, "filename": f.filename, "action": d.action, "reason": d.reason})
    return {"summary": summary, "items": items}


# ---------------------------------------------------------------------------
# Aging policy
# ---------------------------------------------------------------------------


class AgingStage(BaseModel):
    min_days: int = Field(ge=0, le=36500)
    action: Literal["keep", "transcode"]
    profile_id: int | None = None
    priority: int = Field(1, ge=0, le=4)


class AgingPolicyIn(BaseModel):
    library_id: int | None = None
    age_field: Literal["file_age_days", "recorded_age_days"] = "file_age_days"
    stages: list[AgingStage] = Field(min_length=1, max_length=8)
    window: dict | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self) -> AgingPolicyIn:
        days = [s.min_days for s in self.stages]
        if days != sorted(days) or len(set(days)) != len(days):
            raise ValueError("Stages must be in increasing age order without duplicates")
        for s in self.stages:
            if s.action == "transcode" and s.profile_id is None:
                raise ValueError("Every compress stage needs a profile")
        return self


def _group(library_id: int | None) -> str:
    return f"aging-{library_id if library_id is not None else 'all'}"


def _range_label(lo: int, hi: int | None) -> str:
    return f"{lo}+ days" if hi is None else f"{lo}–{hi} days"


def build_aging_rules(body: AgingPolicyIn, profiles: dict[int, Profile], start_position: int) -> list[Rule]:
    """One rule per stage, each matching [min_days, next stage's min_days) of the chosen age field."""
    group = _group(body.library_id)
    rules: list[Rule] = []
    for i, stage in enumerate(body.stages):
        hi = body.stages[i + 1].min_days if i + 1 < len(body.stages) else None
        children = [make_condition(body.age_field, "gte", stage.min_days)]
        if hi is not None:
            children.append(make_condition(body.age_field, "lt", hi))
        if stage.action == "transcode":
            prof = profiles.get(stage.profile_id or -1)
            if prof is None:
                raise HTTPException(404, f"Profile {stage.profile_id} not found")
            name = f"Aging {_range_label(stage.min_days, hi)} → {prof.name}"
        else:
            name = f"Aging {_range_label(stage.min_days, hi)} → keep original"
        rules.append(
            Rule(
                name=name[:128],
                description="Generated by the aging policy",
                library_id=body.library_id,
                position=start_position + i,
                enabled=body.enabled,
                conditions={"type": "group", "op": "all", "children": children},
                action="transcode" if stage.action == "transcode" else "skip",
                profile_id=stage.profile_id if stage.action == "transcode" else None,
                priority=stage.priority,
                schedule={"window": body.window} if body.window else {},
                skip_if_target_codec=True,
                policy_group=group,
            )
        )
    return rules


@router.get("/aging/policy")
async def get_aging(library_id: int | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.get(SystemSetting, f"policy:{_group(library_id)}")
    return {"policy": row.value if row else None}


@router.put("/aging/policy")
async def put_aging(body: AgingPolicyIn, db: AsyncSession = Depends(get_db)) -> dict:
    if body.library_id is not None and await db.get(Library, body.library_id) is None:
        raise HTTPException(404, "Library not found")
    profiles = {p.id: p for p in (await db.execute(select(Profile))).scalars()}
    group = _group(body.library_id)
    old_pos = (await db.execute(select(func.min(Rule.position)).where(Rule.policy_group == group))).scalar_one()
    await db.execute(delete(Rule).where(Rule.policy_group == group))
    if old_pos is None:
        old_pos = (await db.execute(select(func.coalesce(func.max(Rule.position), -1)))).scalar_one() + 1
    created = build_aging_rules(body, profiles, old_pos)
    db.add_all(created)
    key = f"policy:{group}"
    row = await db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=body.model_dump()))
    else:
        row.value = body.model_dump()
    await db.commit()
    for r in created:
        await db.refresh(r, ["profile"])
    return {"policy": body.model_dump(), "rules": [rule_view(r) for r in created]}


@router.delete("/aging/policy")
async def delete_aging(library_id: int | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    group = _group(library_id)
    await db.execute(delete(Rule).where(Rule.policy_group == group))
    row = await db.get(SystemSetting, f"policy:{group}")
    if row:
        await db.delete(row)
    await db.commit()
    return {"ok": True}
