from __future__ import annotations

from itertools import count

from opstriage_contracts.tools import TicketRequest
from opstriage_shared.db import Pool
from opstriage_ticketing.ports import StoredTicket

_EXISTING = """
SELECT ticket_id, status FROM ticketing.tickets
WHERE idempotency_key = %s OR (problem_id = %s AND status = 'open')
ORDER BY (status = 'open') DESC LIMIT 1
"""


class PostgresTicketStore:
    """The v1 'fake' ticketing system: tickets live in PostgreSQL."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def create_if_absent(self, request: TicketRequest) -> tuple[StoredTicket, bool]:
        async with self._pool.connection() as conn:
            for _ in range(3):  # a concurrent insert can win between our read and write
                cur = await conn.execute(_EXISTING, (request.idempotency_key, request.problem_id))
                row = await cur.fetchone()
                if row is not None:
                    return StoredTicket(row["ticket_id"], row["status"]), False
                cur = await conn.execute(
                    "INSERT INTO ticketing.tickets "
                    "(idempotency_key, problem_id, title, description, priority) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING ticket_id, status",
                    (
                        request.idempotency_key,
                        request.problem_id,
                        request.title,
                        request.description,
                        request.priority,
                    ),
                )
                row = await cur.fetchone()
                if row is not None:
                    return StoredTicket(row["ticket_id"], row["status"]), True
        raise RuntimeError("could not create or find ticket")

    async def resolve_open(self, problem_id: str, resolution: str) -> StoredTicket | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE ticketing.tickets SET status = 'resolved', resolution = %s, resolved_at = now() "
                "WHERE problem_id = %s AND status = 'open' RETURNING ticket_id, status",
                (resolution, problem_id),
            )
            row = await cur.fetchone()
        return StoredTicket(row["ticket_id"], row["status"]) if row else None


class InMemoryTicketStore:
    def __init__(self) -> None:
        self._ids = count(1)
        self.tickets: dict[str, dict[str, str]] = {}

    async def create_if_absent(self, request: TicketRequest) -> tuple[StoredTicket, bool]:
        for tid, t in self.tickets.items():
            if t["key"] == request.idempotency_key or (
                t["problem_id"] == request.problem_id and t["status"] == "open"
            ):
                return StoredTicket(tid, t["status"]), False  # type: ignore[arg-type]
        tid = f"TCK-{next(self._ids):06d}"
        self.tickets[tid] = {
            "key": request.idempotency_key,
            "problem_id": request.problem_id,
            "status": "open",
        }
        return StoredTicket(tid, "open"), True

    async def resolve_open(self, problem_id: str, resolution: str) -> StoredTicket | None:
        for tid, t in self.tickets.items():
            if t["problem_id"] == problem_id and t["status"] == "open":
                t["status"] = "resolved"
                return StoredTicket(tid, "resolved")
        return None
