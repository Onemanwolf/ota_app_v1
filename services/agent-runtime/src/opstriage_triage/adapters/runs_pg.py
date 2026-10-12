from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from opstriage_shared.db import Pool

_UPSERT = """
INSERT INTO triage.triage_runs (run_id, problem_id, display_id, event_status, decision, decision_error,
                                target, plan, approval, outcomes)
VALUES (%(run_id)s, %(problem_id)s, %(display_id)s, %(event_status)s, %(decision)s, %(decision_error)s,
        %(target)s, %(plan)s, %(approval)s, %(outcomes)s)
ON CONFLICT (run_id) DO UPDATE SET
    decision = EXCLUDED.decision, decision_error = EXCLUDED.decision_error, target = EXCLUDED.target,
    plan = EXCLUDED.plan, approval = EXCLUDED.approval, outcomes = EXCLUDED.outcomes, finished_at = now()
"""

_JSON_FIELDS = ("decision", "target", "plan", "approval", "outcomes")


class PostgresRunRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def save_run(self, summary: dict[str, Any]) -> None:
        params = {k: (Jsonb(v) if k in _JSON_FIELDS and v is not None else v) for k, v in summary.items()}
        async with self._pool.connection() as conn:
            await conn.execute(_UPSERT, params)

    async def append_audit(self, run_id: str, kind: str, detail: dict[str, Any]) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO triage.audit_log (run_id, kind, detail) VALUES (%s, %s, %s)",
                (run_id, kind, Jsonb(detail)),
            )

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM triage.triage_runs WHERE run_id = %s", (run_id,))
            row = await cur.fetchone()
            if row is None:
                return None
            cur = await conn.execute(
                "SELECT kind, detail, recorded_at FROM triage.audit_log WHERE run_id = %s ORDER BY id",
                (run_id,),
            )
            audit = await cur.fetchall()
        return {
            **row,
            "finished_at": row["finished_at"].isoformat(),
            "audit": [{**a, "recorded_at": a["recorded_at"].isoformat()} for a in audit],
        }
