"""Data access for saved ticket analyses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TicketAnalysisRecord, User


async def save(session: AsyncSession, record: TicketAnalysisRecord) -> TicketAnalysisRecord:
    session.add(record)
    await session.flush()
    return record


async def list_records(
    session: AsyncSession, *, user_id: uuid.UUID | None, offset: int, limit: int
) -> tuple[int, list[tuple[TicketAnalysisRecord, str]]]:
    """Newest first, with the owner's email. `user_id=None` lists every user's analyses."""
    where = [] if user_id is None else [TicketAnalysisRecord.user_id == user_id]
    total = int(
        await session.scalar(select(func.count()).select_from(TicketAnalysisRecord).where(*where))
        or 0
    )
    rows = await session.execute(
        select(TicketAnalysisRecord, User.email)
        .join(User, User.id == TicketAnalysisRecord.user_id)
        .where(*where)
        .order_by(TicketAnalysisRecord.created_at.desc(), TicketAnalysisRecord.id)
        .offset(offset)
        .limit(limit)
    )
    return total, [(record, email) for record, email in rows.all()]


async def get(
    session: AsyncSession, record_id: uuid.UUID
) -> tuple[TicketAnalysisRecord, str] | None:
    row = (
        await session.execute(
            select(TicketAnalysisRecord, User.email)
            .join(User, User.id == TicketAnalysisRecord.user_id)
            .where(TicketAnalysisRecord.id == record_id)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def delete_record(session: AsyncSession, record_id: uuid.UUID) -> None:
    await session.execute(delete(TicketAnalysisRecord).where(TicketAnalysisRecord.id == record_id))


async def stats_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    rows = await session.execute(
        select(TicketAnalysisRecord.status, func.count(), func.max(TicketAnalysisRecord.created_at))
        .where(TicketAnalysisRecord.user_id == user_id)
        .group_by(TicketAnalysisRecord.status)
    )
    by_status: dict[str, int] = {}
    last: datetime | None = None
    for status, count, latest in rows.all():
        by_status[status] = int(count)
        if latest is not None and (last is None or latest > last):
            last = latest
    return {"by_status": by_status, "last_analysis_at": last}


async def prune(session: AsyncSession, retention_days: int) -> int:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    result = await session.execute(
        delete(TicketAnalysisRecord).where(TicketAnalysisRecord.created_at < cutoff)
    )
    return int(getattr(result, "rowcount", 0) or 0)
