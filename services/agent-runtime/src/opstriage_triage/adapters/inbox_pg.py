"""InboxPort adapter. The SQL is owned by the Intake context (spec 10.2): a documented, narrow exception to
"no cross-schema access", equivalent to consuming a queue."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

import psycopg

from opstriage_contracts.problems import ProblemEvent
from opstriage_shared.db import Pool
from opstriage_triage.ports import ClaimedAlert

_CLAIM = """
UPDATE intake.alert_inbox AS a
SET state = 'processing', attempts = a.attempts + 1, locked_until = now() + make_interval(secs => %(lease)s)
WHERE a.alert_id = (
    SELECT alert_id FROM intake.alert_inbox
    WHERE ((state = 'pending' AND (locked_until IS NULL OR locked_until < now()))
           OR (state = 'processing' AND locked_until < now()))
      AND attempts < %(max_attempts)s
    ORDER BY received_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING a.alert_id, a.event, a.attempts
"""

_FAIL = """
UPDATE intake.alert_inbox
SET state = CASE WHEN attempts >= %(max_attempts)s THEN 'failed' ELSE 'pending' END,
    -- pending rows wait out a backoff that grows with each attempt before they can be claimed again
    locked_until = CASE WHEN attempts >= %(max_attempts)s THEN NULL
                        ELSE now() + make_interval(secs => %(backoff)s * attempts) END,
    last_error = %(error)s
WHERE alert_id = %(alert_id)s
RETURNING state
"""

_SWEEP = """
UPDATE intake.alert_inbox
SET state = 'failed', last_error = coalesce(last_error, 'lease expired on last attempt')
WHERE state = 'processing' AND locked_until < now() AND attempts >= %s
RETURNING alert_id
"""


class PostgresInbox:
    def __init__(
        self,
        pool: Pool,
        listen_connect: Callable[[], Awaitable[psycopg.AsyncConnection[Any]]],
        *,
        lease_seconds: int,
        max_attempts: int,
        retry_backoff_seconds: float = 10.0,
    ) -> None:
        self._pool, self._connect = pool, listen_connect  # opstriage_shared.db.connect: same auth as the pool
        self._lease, self._max, self._backoff = lease_seconds, max_attempts, retry_backoff_seconds
        self._listener: psycopg.AsyncConnection[Any] | None = None
        self._listen_lock = asyncio.Lock()

    async def claim_next(self) -> ClaimedAlert | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(_CLAIM, {"lease": self._lease, "max_attempts": self._max})
            row = await cur.fetchone()
        if row is None:
            return None
        return ClaimedAlert(
            alert_id=row["alert_id"],
            event=ProblemEvent.model_validate(row["event"]),
            attempts=row["attempts"],
        )

    async def wait_for_notify(self, max_wait: float) -> None:
        """LISTEN with a timeout. NOTIFY is not durable, so callers also poll."""
        async with self._listen_lock:
            try:
                if self._listener is None or self._listener.closed:
                    self._listener = await self._connect()
                    await self._listener.execute("LISTEN alert_inbox")
                async for _ in self._listener.notifies(timeout=max_wait, stop_after=1):
                    return
            except psycopg.OperationalError:
                await self.close()
                await asyncio.sleep(max_wait)

    async def mark(self, alert_id: UUID, state: Literal["done", "awaiting_approval"]) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE intake.alert_inbox SET state = %s, locked_until = NULL WHERE alert_id = %s",
                (state, alert_id),
            )

    async def fail(self, alert_id: UUID, error: str) -> Literal["pending", "failed"]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                _FAIL,
                {
                    "max_attempts": self._max,
                    "error": error[:500],
                    "alert_id": alert_id,
                    "backoff": self._backoff,
                },
            )
            row = await cur.fetchone()
        return "failed" if row is None or row["state"] == "failed" else "pending"

    async def sweep_expired(self) -> list[UUID]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(_SWEEP, (self._max,))
            return [r["alert_id"] for r in await cur.fetchall()]

    async def supersede_pending(self, problem_id: str, *, except_alert_id: UUID) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE intake.alert_inbox SET state = 'superseded' "
                "WHERE problem_id = %s AND alert_id <> %s AND state = 'pending'",
                (problem_id, except_alert_id),
            )
            return cur.rowcount

    async def latest_event(self, problem_id: str) -> ProblemEvent | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT event FROM intake.alert_inbox WHERE problem_id = %s "
                "ORDER BY received_at DESC LIMIT 1",
                (problem_id,),
            )
            row = await cur.fetchone()
        return ProblemEvent.model_validate(row["event"]) if row else None

    async def get_event(self, alert_id: UUID) -> ProblemEvent | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT event FROM intake.alert_inbox WHERE alert_id = %s", (alert_id,))
            row = await cur.fetchone()
        return ProblemEvent.model_validate(row["event"]) if row else None

    async def close(self) -> None:
        if self._listener is not None:
            with contextlib.suppress(Exception):
                await self._listener.close()
            self._listener = None
