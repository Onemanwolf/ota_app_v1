from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from opstriage_contracts.tools import RemediationStatus
from opstriage_remediation.domain.execution import Execution
from opstriage_shared.db import Pool

_COLUMNS = (
    "idempotency_key, target_id, problem_id, status, reason, executor, approved_by, "
    "started_at, finished_at, output_tail"
)


class PostgresExecutionStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def get(self, idempotency_key: str) -> Execution | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLUMNS} FROM remediation.executions WHERE idempotency_key = %s",  # noqa: S608 (constant)
                (idempotency_key,),
            )
            row = await cur.fetchone()
        return Execution(**{**row, "status": RemediationStatus(row["status"])}) if row else None

    async def insert(self, execution: Execution) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"INSERT INTO remediation.executions ({_COLUMNS}) "  # noqa: S608 (constant)
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (idempotency_key) DO NOTHING RETURNING idempotency_key",
                (
                    execution.idempotency_key,
                    execution.target_id,
                    execution.problem_id,
                    execution.status.value,
                    execution.reason,
                    execution.executor,
                    execution.approved_by,
                    execution.started_at,
                    execution.finished_at,
                    execution.output_tail,
                ),
            )
            return await cur.fetchone() is not None

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
