"""Persist one metadata row per API request to `request_logs` (observability, ADR D12).

Rows hold the route template (never the concrete path, so no IDs or query strings), the
status, latency, error code, and for ticket analysis the stage timings and an outcome
summary (status, category, priority, grounding, review reasons). Ticket text, retrieved
content and model output are never stored.

Writes are off the request path: the middleware enqueues, one background task inserts in
batches. When the queue is full, entries are dropped and counted rather than slowing
requests down; a database outage never fails a request.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import RequestLog

logger = logging.getLogger("app.request_log")

# Polled by Docker health checks every few seconds: logging them would drown real traffic.
SKIPPED_ROUTES = frozenset({"/health", "/ready"})


class RequestLogWriter:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_queue: int = 1000,
        batch_size: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max_queue)
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self.dropped = 0

    def submit(self, entry: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            self.dropped += 1

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="request-log-writer")

    async def flush(self) -> None:
        """Wait until everything submitted so far is written (used by tests and shutdown)."""
        await self._queue.join()

    async def stop(self) -> None:
        if self._task is None:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.flush(), timeout=5)
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def prune(self, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with self._session_factory() as session, session.begin():
            result = await session.execute(delete(RequestLog).where(RequestLog.created_at < cutoff))
        return int(getattr(result, "rowcount", 0) or 0)

    async def _run(self) -> None:
        while True:
            batch = [await self._queue.get()]
            while len(batch) < self._batch_size and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            try:
                async with self._session_factory() as session, session.begin():
                    await session.execute(insert(RequestLog), batch)
            except Exception as exc:  # the log store must never take requests down
                logger.warning(
                    "request log write failed",
                    extra={"rows": len(batch), "error": type(exc).__name__},
                )
            finally:
                for _ in batch:
                    self._queue.task_done()
