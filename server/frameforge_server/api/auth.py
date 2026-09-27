"""Authentication endpoints: first-run setup, login, logout, password change."""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.security import MIN_PASSWORD_LENGTH, hash_password, needs_rehash, verify_password
from ..auth.sessions import SESSION_COOKIE, any_users, create_session, destroy_session, optional_user, require_user
from ..config import get_settings
from ..db.models import Session, User
from ..db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

_FAIL_WINDOW = 300
_FAIL_LIMIT = 10
_failures: dict[str, list[float]] = defaultdict(list)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SetupRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


def _user_view(user: User) -> dict:
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=s.session_days * 86400, httponly=True, samesite="lax", secure=s.secure_cookies, path="/"
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    recent = [t for t in _failures[ip] if now - t < _FAIL_WINDOW]
    _failures[ip] = recent
    if len(recent) >= _FAIL_LIMIT:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed sign-in attempts. Try again in a few minutes.")


@router.get("/status")
async def auth_status(user: User | None = Depends(optional_user), db: AsyncSession = Depends(get_db)) -> dict:
    return {"setup_required": not await any_users(db), "authenticated": user is not None, "user": _user_view(user) if user else None}


@router.post("/setup")
async def setup(body: SetupRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    if await any_users(db):
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup has already been completed")
    user = User(username=body.username, password_hash=hash_password(body.password), is_admin=True)
    db.add(user)
    await db.commit()
    token = await create_session(db, user, request.headers.get("user-agent"), _client_ip(request))
    _set_cookie(response, token)
    return {"user": _user_view(user)}


@router.post("/login")
async def login(body: Credentials, request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    ip = _client_ip(request)
    _check_rate_limit(ip)
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None or not verify_password(user.password_hash, body.password):
        _failures[ip].append(time.monotonic())
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token = await create_session(db, user, request.headers.get("user-agent"), ip)
    _set_cookie(response, token)
    return {"user": _user_view(user)}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await destroy_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/password")
async def change_password(body: PasswordChange, request: Request, user: User = Depends(require_user), db: AsyncSession = Depends(get_db)) -> dict:
    db_user = await db.get(User, user.id)
    if db_user is None or not verify_password(db_user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    db_user.password_hash = hash_password(body.new_password)
    # Sign out every other session.
    current = request.cookies.get(SESSION_COOKIE)
    from ..auth.security import token_hash

    await db.execute(delete(Session).where(Session.user_id == user.id, Session.id != token_hash(current or "")))
    await db.commit()
    return {"ok": True}
