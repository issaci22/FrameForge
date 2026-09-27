"""Node management: enrollment, settings, live state."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from frameforge_shared.protocol import NodeCapabilities

from ..auth.security import new_token, token_hash
from ..auth.sessions import require_user
from ..config import get_settings
from ..db.models import ACTIVE_JOB_STATES, Job, Library, Node
from ..db.session import get_db
from ..logging_setup import tail_file
from ..services.node_compose import compose_snippet, docker_run_snippet
from ..services.node_manager import manager
from ..services.scheduler import scheduler
from ..services.system_settings import load_settings

router = APIRouter(prefix="/nodes", tags=["nodes"], dependencies=[Depends(require_user)])

Hardware = Literal["cpu", "nvidia", "intel", "amd"]


class TimeWindowIn(BaseModel):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])


class Constraints(BaseModel):
    max_gpu_util: int | None = Field(None, ge=1, le=100)
    max_cpu_util: int | None = Field(None, ge=1, le=100)
    window: TimeWindowIn | None = None


class PathMappingIn(BaseModel):
    server: str
    node: str

    @field_validator("server", "node")
    @classmethod
    def _abs(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if not v.startswith("/"):
            raise ValueError("Paths must be absolute")
        return v.rstrip("/") or "/"


class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    hardware: Hardware = "cpu"
    max_concurrency: int = Field(1, ge=1, le=16)
    path_mappings: list[PathMappingIn] = Field(default_factory=list)


class NodeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=64)
    enabled: bool | None = None
    paused: bool | None = None
    max_concurrency: int | None = Field(None, ge=1, le=16)
    reserve_slot_for_normal: bool | None = None
    constraints: Constraints | None = None
    path_mappings: list[PathMappingIn] | None = None


def node_view(node: Node) -> dict:
    caps = NodeCapabilities.model_validate(node.capabilities) if node.capabilities else None
    live = manager.live_view(node.id)
    return {
        "id": node.id,
        "name": node.name,
        "is_local": node.is_local,
        "enabled": node.enabled,
        "paused": node.paused,
        "online": live["online"],
        "status": "disabled" if not node.enabled else ("paused" if node.paused and live["online"] else ("online" if live["online"] else ("pending" if node.last_seen_at is None else "offline"))),
        "max_concurrency": node.max_concurrency,
        "reserve_slot_for_normal": node.reserve_slot_for_normal,
        "constraints": node.constraints or {},
        "path_mappings": node.path_mappings or [],
        "hardware_hint": node.hardware_hint,
        "token_hint": node.token_hint,
        "version": node.version,
        "created_at": node.created_at.isoformat(),
        "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at else None,
        "capabilities": caps.model_dump(mode="json") if caps else None,
        "metrics": live.get("metrics") or (node.last_metrics or None),
        "active_jobs": live.get("active_jobs", []),
    }


async def _server_url(request: Request) -> str:
    settings = await load_settings()
    if settings.public_url:
        return settings.public_url.rstrip("/")
    return str(request.base_url).rstrip("/")


async def _media_paths(db: AsyncSession) -> list[str]:
    paths: list[str] = []
    for lib in (await db.execute(select(Library))).scalars():
        for p in lib.paths or []:
            if p not in paths:
                paths.append(p)
    return paths


@router.get("")
async def list_nodes(db: AsyncSession = Depends(get_db)) -> list[dict]:
    nodes = (await db.execute(select(Node).order_by(Node.is_local.desc(), Node.name))).scalars().all()
    return [node_view(n) for n in nodes]


@router.post("", status_code=201)
async def create_node(body: NodeCreate, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    if (await db.execute(select(Node).where(Node.name == body.name))).scalar_one_or_none():
        raise HTTPException(409, "A node with that name already exists")
    token = new_token("ffn_")
    node = Node(
        name=body.name,
        token_hash=token_hash(token),
        token_hint=token[:8],
        max_concurrency=body.max_concurrency,
        hardware_hint=body.hardware,
        path_mappings=[m.model_dump() for m in body.path_mappings],
    )
    db.add(node)
    await db.commit()
    url = await _server_url(request)
    media = await _media_paths(db)
    return {
        "node": node_view(node),
        "token": token,
        "server_url": url,
        "compose": compose_snippet(url, token, body.hardware, media),
        "docker_run": docker_run_snippet(url, token, body.hardware, media),
    }


async def _get(db: AsyncSession, node_id: int) -> Node:
    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(404, "Node not found")
    return node


@router.get("/{node_id}")
async def get_node(node_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    node = await _get(db, node_id)
    view = node_view(node)
    view["history"] = manager.history(node_id)
    return view


@router.patch("/{node_id}")
async def update_node(node_id: int, body: NodeUpdate, db: AsyncSession = Depends(get_db)) -> dict:
    node = await _get(db, node_id)
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != node.name:
        if (await db.execute(select(Node).where(Node.name == data["name"]))).scalar_one_or_none():
            raise HTTPException(409, "A node with that name already exists")
    if "constraints" in data:
        data["constraints"] = body.constraints.model_dump(exclude_none=True) if body.constraints else {}
    if "path_mappings" in data:
        data["path_mappings"] = [m.model_dump() for m in body.path_mappings or []]
    for key, value in data.items():
        setattr(node, key, value)
    await db.commit()
    await manager.push_config(node)
    scheduler.wake()
    return node_view(node)


@router.post("/{node_id}/token")
async def regenerate_token(node_id: int, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    node = await _get(db, node_id)
    if node.is_local:
        raise HTTPException(400, "The built-in node manages its own token")
    token = new_token("ffn_")
    node.token_hash = token_hash(token)
    node.token_hint = token[:8]
    await db.commit()
    await manager.disconnect(node.id, "Token was regenerated")
    url = await _server_url(request)
    hw = node.hardware_hint if node.hardware_hint in ("cpu", "nvidia", "intel", "amd") else "cpu"
    media = await _media_paths(db)
    return {"token": token, "server_url": url, "compose": compose_snippet(url, token, hw, media), "docker_run": docker_run_snippet(url, token, hw, media)}  # type: ignore[arg-type]


@router.post("/{node_id}/redetect")
async def redetect(node_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    await _get(db, node_id)
    if not await manager.request_redetect(node_id):
        raise HTTPException(409, "The node is offline")
    return {"ok": True}


@router.delete("/{node_id}")
async def delete_node(node_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    node = await _get(db, node_id)
    if node.is_local:
        raise HTTPException(400, "The built-in node can't be removed. Disable it instead.")
    busy = (await db.execute(select(Job.id).where(Job.node_id == node_id, Job.state.in_(ACTIVE_JOB_STATES)).limit(1))).first()
    if busy:
        raise HTTPException(409, "This node is still working on a job. Pause it and wait, or cancel its jobs first.")
    await manager.disconnect(node_id, "Node removed")
    await db.delete(node)
    await db.commit()
    return {"ok": True}


@router.get("/{node_id}/logs", response_class=PlainTextResponse)
async def node_logs(node_id: int, lines: int = Query(500, ge=10, le=20000)) -> str:
    path = get_settings().node_logs_dir / f"node-{node_id}.log"
    return "\n".join(await asyncio.to_thread(tail_file, path, lines))
