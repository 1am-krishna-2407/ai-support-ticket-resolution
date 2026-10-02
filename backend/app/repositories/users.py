"""Data access for users and their sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TicketAnalysisRecord, User, UserRole, UserSession


def normalize_email(email: str) -> str:
    return email.strip().lower()


async def get_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)


async def create_user(
    session: AsyncSession, *, email: str, name: str, password_hash: str, role: UserRole
) -> User:
    user = User(
        email=normalize_email(email),
        name=name.strip(),
        password_hash=password_hash,
        role=role.value,
        is_active=True,
        password_changed_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def list_users(
    session: AsyncSession, *, offset: int, limit: int
) -> tuple[int, list[tuple[User, int]]]:
    """Users ordered by name, each with the number of analyses they have saved."""
    total = int(await session.scalar(select(func.count()).select_from(User)) or 0)
    counts = (
        select(TicketAnalysisRecord.user_id, func.count().label("n"))
        .group_by(TicketAnalysisRecord.user_id)
        .subquery()
    )
    rows = await session.execute(
        select(User, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.user_id == User.id)
        .order_by(User.name, User.email)
        .offset(offset)
        .limit(limit)
    )
    return total, [(user, int(n)) for user, n in rows.all()]


async def count_active_admins(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN.value, User.is_active.is_(True))
        )
        or 0
    )


# --- sessions -------------------------------------------------------------------------


async def create_session(
    session: AsyncSession, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
) -> UserSession:
    row = UserSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
    session.add(row)
    await session.flush()
    return row


async def get_session(session: AsyncSession, token_hash: str) -> UserSession | None:
    result = await session.execute(select(UserSession).where(UserSession.token_hash == token_hash))
    return result.unique().scalar_one_or_none()


async def delete_session(session: AsyncSession, token_hash: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))


async def delete_user_sessions(
    session: AsyncSession, user_id: uuid.UUID, *, keep_token_hash: str | None = None
) -> None:
    """Sign a user out everywhere (optionally keeping the current browser)."""
    statement = delete(UserSession).where(UserSession.user_id == user_id)
    if keep_token_hash is not None:
        statement = statement.where(UserSession.token_hash != keep_token_hash)
    await session.execute(statement)


async def purge_expired_sessions(session: AsyncSession) -> int:
    result = await session.execute(
        delete(UserSession).where(UserSession.expires_at <= datetime.now(UTC))
    )
    return int(getattr(result, "rowcount", 0) or 0)
