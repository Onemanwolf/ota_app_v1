from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from opstriage_shared.db import Pool
from opstriage_triage.ports import ApprovalMode, ApprovalRecord, ApprovalState

_COLS = (
    "approval_id, run_id, problem_id, target_id, state, decided_by, reason, requested_at, decided_at, "
    "resumed_at"
)

_DECIDE = """
UPDATE triage.approval_requests SET state = %s, decided_by = %s, reason = %s, decided_at = now()
WHERE run_id = %s AND state = 'requested'
RETURNING approval_id, run_id, problem_id, target_id, state, decided_by, reason, requested_at,
          decided_at, resumed_at
"""


def _record(row: dict[str, Any]) -> ApprovalRecord:
    return ApprovalRecord(**row)


class PostgresApprovalStore:
    def __init__(self, pool: Pool, mode: ApprovalMode) -> None:
        self._pool, self._mode = pool, mode

    @property
    def mode(self) -> ApprovalMode:
        return self._mode

    async def open(self, *, run_id: str, problem_id: str, target_id: str) -> ApprovalRecord:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO triage.approval_requests (approval_id, run_id, problem_id, target_id) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (run_id) DO NOTHING",
                (uuid4(), run_id, problem_id, target_id),
            )
            cur = await conn.execute(
                f"SELECT {_COLS} FROM triage.approval_requests WHERE run_id = %s",  # noqa: S608
                (run_id,),
            )
            row = await cur.fetchone()
        assert row is not None  # noqa: S101 (inserted or existing)
        return _record(row)

    async def get(self, run_id: str) -> ApprovalRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM triage.approval_requests WHERE run_id = %s",  # noqa: S608
                (run_id,),
            )
            row = await cur.fetchone()
        return _record(row) if row else None

    async def decide(
        self,
        run_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str | None,
        new_state: ApprovalState | None = None,
    ) -> ApprovalRecord | None:
        state = new_state or ("approved" if approved else "rejected")
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                _DECIDE,
                (state, decided_by, reason, run_id),
            )
            row = await cur.fetchone()
        return _record(row) if row else None

    async def cancel_for_problem(self, problem_id: str, reason: str) -> list[str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE triage.approval_requests "
                "SET state = 'cancelled', decided_by = 'system', reason = %s, "
                "decided_at = now() WHERE problem_id = %s AND state = 'requested' RETURNING run_id",
                (reason, problem_id),
            )
            return [r["run_id"] for r in await cur.fetchall()]

    async def expire_older_than(self, cutoff: datetime) -> list[str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE triage.approval_requests "
                "SET state = 'expired', decided_by = 'system', reason = 'expired', "
                "decided_at = now() WHERE state = 'requested' AND requested_at < %s RETURNING run_id",
                (cutoff,),
            )
            return [r["run_id"] for r in await cur.fetchall()]

    async def decided_not_resumed(self) -> list[ApprovalRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_COLS} FROM triage.approval_requests "  # noqa: S608
                "WHERE state <> 'requested' AND resumed_at IS NULL ORDER BY decided_at"
            )
            return [_record(r) for r in await cur.fetchall()]

    async def mark_resumed(self, run_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE triage.approval_requests SET resumed_at = now() WHERE run_id = %s", (run_id,)
            )
