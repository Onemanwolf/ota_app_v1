from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from opstriage_contracts.problems import ProblemEvent
from opstriage_intake.domain.alert import AlertState, AlertView
from opstriage_shared.db import Pool

_INSERT = """
INSERT INTO intake.alert_inbox (alert_id, dedup_key, display_key, source, problem_id, display_id,
                                status, event, raw_payload)
VALUES (%(alert_id)s, %(dedup_key)s, %(display_key)s, %(source)s, %(problem_id)s, %(display_id)s,
        %(status)s, %(event)s, %(raw_payload)s)
ON CONFLICT DO NOTHING
RETURNING alert_id
"""

_GET = """
SELECT alert_id, problem_id, display_id, status, source, state, attempts, received_at, last_error
FROM intake.alert_inbox WHERE alert_id = %s
"""


class PostgresInboxRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def add_if_new(self, *, alert_id: UUID, event: ProblemEvent, raw: dict[str, Any] | None) -> bool:
        params = {
            "alert_id": alert_id,
            "dedup_key": event.dedup_key,
            "display_key": f"{event.display_id}:{event.status.value}" if event.display_id else None,
            "source": event.source.value,
            "problem_id": event.problem_id,
            "display_id": event.display_id,
            "status": event.status.value,
            "event": Jsonb(event.model_dump(mode="json")),
            "raw_payload": Jsonb(raw) if raw is not None else None,
        }
        async with self._pool.connection() as conn:
            cur = await conn.execute(_INSERT, params)
            return await cur.fetchone() is not None

    async def notify_new(self) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("NOTIFY alert_inbox")

    async def get(self, alert_id: UUID) -> AlertView | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(_GET, (alert_id,))
            row = await cur.fetchone()
        if row is None:
            return None
        return AlertView(**{**row, "state": AlertState(row["state"])})


async def grant_consumer(pool: Pool, role: str) -> None:
    """The inbox belongs to Intake; the triage worker consumes it through a narrow grant (spec 10.2)."""
    from psycopg import sql

    ident = sql.Identifier(role)
    async with pool.connection() as conn:
        await conn.execute(sql.SQL("GRANT USAGE ON SCHEMA intake TO {}").format(ident))
        await conn.execute(sql.SQL("GRANT SELECT, UPDATE ON intake.alert_inbox TO {}").format(ident))
