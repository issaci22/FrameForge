"""Opaque server-side sessions + the authentication seam used by the API."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Session, User
from ..db.session import get_db, sessionmaker
from ..db.types import utcnow
from .security import new_token, token_hash

SESSION_COOKIE = "ff_session"
_TOUCH_INTERVAL = timedelta(minutes=5)


class AuthProvider(Protocol):
    """Resolve the current user from a request. Future: OIDC, API tokens, reverse-proxy headers."""

    async def authenticate(self, token: str | None, db: AsyncSession) -> User | None: ...


class SessionAuthProvider:
    async def authenticate(self, token: str | None, db: AsyncSession) -> User | None:
        if not token:
            return None
        session = await db.get(Session, token_hash(token))
        now = utcnow()
        if session is None or session.expires_at < now:
            return None
        if now - session.last_seen_at > _TOUCH_INTERVAL:
            session.last_seen_at = now
            session.expires_at = now + timedelta(days=get_settings().session_days)
            await db.commit()
        return session.user


providers: list[AuthProvider] = [SessionAuthProvider()]


async def create_session(db: AsyncSession, user: User, user_agent: str | None, ip: str | None) -> str:
    token = new_token()
    now = utcnow()
    db.add(
        Session(
            id=token_hash(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + timedelta(days=get_settings().session_days),
            user_agent=(user_agent or "")[:255],
            ip=ip,
        )
    )
    user.last_login_at = now
    await db.commit()
    return token


async def destroy_session(db: AsyncSession, token: str) -> None:
    await db.execute(delete(Session).where(Session.id == token_hash(token)))
    await db.commit()


async def purge_expired_sessions() -> None:
    async with sessionmaker()() as db:
        await db.execute(delete(Session).where(Session.expires_at < utcnow()))
        await db.commit()


async def _resolve(token: str | None, db: AsyncSession) -> User | None:
    for provider in providers:
        user = await provider.authenticate(token, db)
        if user is not None:
            return user
    return None


async def optional_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    return await _resolve(request.cookies.get(SESSION_COOKIE), db)


async def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


async def websocket_user(ws: WebSocket) -> User | None:
    async with sessionmaker()() as db:
        return await _resolve(ws.cookies.get(SESSION_COOKIE), db)


async def any_users(db: AsyncSession) -> bool:
    return (await db.execute(select(User.id).limit(1))).first() is not None
