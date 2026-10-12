from __future__ import annotations

from psycopg.types.json import Jsonb

from opstriage_contracts.tools import ChannelOutcome, NotificationResult
from opstriage_shared.db import Pool


class PostgresDeliveryStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def get(self, idempotency_key: str) -> NotificationResult | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT outcomes FROM notification.deliveries WHERE idempotency_key = %s", (idempotency_key,)
            )
            row = await cur.fetchone()
        if row is None:
            return None
        outcomes = tuple(ChannelOutcome.model_validate(o) for o in row["outcomes"])
        return NotificationResult(idempotency_key=idempotency_key, outcomes=outcomes)

    async def save(self, result: NotificationResult, *, kind: str, severity: str, title: str) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO notification.deliveries (idempotency_key, kind, severity, title, outcomes) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING idempotency_key",
                (
                    result.idempotency_key,
                    kind,
                    severity,
                    title,
                    Jsonb([o.model_dump(mode="json") for o in result.outcomes]),
                ),
            )
            return await cur.fetchone() is not None


class InMemoryDeliveryStore:
    def __init__(self) -> None:
        self.results: dict[str, NotificationResult] = {}

    async def get(self, idempotency_key: str) -> NotificationResult | None:
        return self.results.get(idempotency_key)

    async def save(self, result: NotificationResult, *, kind: str, severity: str, title: str) -> bool:
        if result.idempotency_key in self.results:
            return False
        self.results[result.idempotency_key] = result
        return True
