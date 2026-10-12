from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from psycopg import errors

from opstriage_contracts.tools import RemediationStatus
from opstriage_remediation.domain.execution import Execution
from opstriage_remediation.ports.store import StartOutcome
from opstriage_shared.db import Pool

_SELECT = """
SELECT idempotency_key, target_id, problem_id, status, reason, executor, approved_by, started_at, finished_at,
       output_tail
FROM remediation.executions WHERE idempotency_key = %s
"""

_INSERT = """
INSERT INTO remediation.executions (idempotency_key, target_id, problem_id, status, reason, executor,
                                    approved_by, started_at, finished_at, output_tail)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _row(e: Execution) -> tuple[object, ...]:
    return (
        e.idempotency_key,
        e.target_id,
        e.problem_id,
        e.status.value,
        e.reason,
        e.executor,
        e.approved_by,
        e.started_at,
        e.finished_at,
        e.output_tail,
    )


class PostgresExecutionStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def get(self, idempotency_key: str) -> Execution | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(_SELECT, (idempotency_key,))
            row = await cur.fetchone()
        return Execution(**{**row, "status": RemediationStatus(row["status"])}) if row else None

    async def insert(self, execution: Execution) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                _INSERT + " ON CONFLICT (idempotency_key) DO NOTHING RETURNING 1", _row(execution)
            )
            return await cur.fetchone() is not None

    async def start(self, execution: Execution) -> StartOutcome:
        """The insert is the lock. Tell the two unique constraints apart by name (spec 12.5.2 step 5)."""
        try:
            async with self._pool.connection() as conn:
                await conn.execute(_INSERT, _row(execution))
        except errors.UniqueViolation as exc:
            if exc.diag.constraint_name == "executions_one_inflight_per_target":
                return "busy"
            return "duplicate"
        return "started"

    async def finish(
        self,
        idempotency_key: str,
        *,
        status: RemediationStatus,
        finished_at: datetime,
        reason: str | None,
        output_tail: str | None,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE remediation.executions "
                "SET status = %s, finished_at = %s, reason = %s, output_tail = %s "
                "WHERE idempotency_key = %s AND status = 'in_progress'",
                (status.value, finished_at, reason, output_tail, idempotency_key),
            )

    async def record_rejection(
        self, *, idempotency_key: str, target_id: str, problem_id: str, reason: str
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO remediation.rejections (idempotency_key, target_id, problem_id, reason) "
                "VALUES (%s, %s, %s, %s)",
                (idempotency_key, target_id, problem_id, reason),
            )

    async def count_since(
        self, target_id: str, since: datetime, statuses: Collection[RemediationStatus]
    ) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM remediation.executions "
                "WHERE target_id = %s AND started_at >= %s AND status = ANY(%s)",
                (target_id, since, [s.value for s in statuses]),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def failures_since(self, since: datetime) -> int:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM remediation.executions "
                "WHERE status = 'failed' AND started_at >= %s",
                (since,),
            )
            row = await cur.fetchone()
        return int(row["n"]) if row else 0

    async def last_breaker_reset(self) -> datetime | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT max(reset_at) AS at FROM remediation.breaker_resets")
            row = await cur.fetchone()
        return row["at"] if row else None

    async def reset_breaker(self, *, operator: str, reason: str | None) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO remediation.breaker_resets (reset_by, reason) VALUES (%s, %s)",
                (operator, reason),
            )

    async def fail_stale(self, target_id: str, started_before: datetime, reason: str) -> list[str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE remediation.executions SET status = 'failed', reason = %s, finished_at = now() "
                "WHERE target_id = %s AND status = 'in_progress' AND started_at < %s "
                "RETURNING idempotency_key",
                (reason, target_id, started_before),
            )
            return [r["idempotency_key"] for r in await cur.fetchall()]
