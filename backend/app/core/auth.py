"""Sign-in sessions and authorization dependencies.

A session is an opaque random token in an HttpOnly, SameSite=Strict cookie scoped to the
API path; the database stores only its SHA-256, so a leaked database cannot be replayed.
Sessions expire after SESSION_TTL_HOURS and are deleted on sign-out, password change,
role change, or deactivation, so revocation takes effect on the next request.

Roles: `agent` analyses tickets and sees their own history; `admin` additionally manages
users, starts ingestion and evaluation runs, and sees every user's history.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError, ServiceUnavailableError, UnauthorizedError
from app.db.models import User, UserRole
from app.repositories import users as users_repo

COOKIE_PATH = "/api"
# last_seen_at is refreshed at most this often, so reads do not write on every request.
_TOUCH_INTERVAL = timedelta(minutes=5)
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def start_session(
    db: AsyncSession, response: Response, user: User, settings: Settings
) -> None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    await users_repo.create_session(
        db, user_id=user.id, token_hash=hash_token(token), expires_at=expires
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
    )


def session_token(request: Request, settings: Settings) -> str | None:
    token = request.cookies.get(settings.session_cookie_name)
    return token if token and len(token) <= 128 else None


async def current_user_optional(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> User | None:
    """The signed-in user, or None. The returned User is detached (a read-only snapshot).

    The lookup uses its own short session, so nothing touches the database without a
    cookie, and no connection is held while a route waits minutes for an LLM.
    """
    token = session_token(request, settings)
    if token is None:
        return None
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with factory() as db:
            row = await users_repo.get_session(db, hash_token(token))
            now = datetime.now(UTC)
            if row is None or row.expires_at <= now or not row.user.is_active:
                return None
            if row.last_seen_at is None or now - row.last_seen_at > _TOUCH_INTERVAL:
                row.last_seen_at = now
                await db.commit()
            user = row.user
    except (SQLAlchemyError, OSError) as exc:
        raise ServiceUnavailableError(
            "Sign-in cannot be checked: the database is unavailable."
        ) from exc
    request.state.user_id = user.id  # for the request log's audit trail
    return user


async def require_user(
    user: Annotated[User | None, Depends(current_user_optional)],
) -> User:
    if user is None:
        raise UnauthorizedError("Sign in to continue.")
    return user


CurrentUser = Annotated[User, Depends(require_user)]


async def require_admin_user(user: CurrentUser) -> User:
    if user.role != UserRole.ADMIN.value:
        raise ForbiddenError("This requires an admin account.")
    return user


AdminUser = Annotated[User, Depends(require_admin_user)]


async def reject_cross_site(request: Request) -> None:
    """Defence in depth on top of SameSite=Strict: browsers label cross-site requests."""
    if request.method in _UNSAFE_METHODS and request.headers.get("sec-fetch-site") == "cross-site":
        raise ForbiddenError("Cross-site requests are not allowed.")


def is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN.value
