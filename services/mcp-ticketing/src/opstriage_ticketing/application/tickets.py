from __future__ import annotations

from opstriage_contracts.tools import TicketRequest, TicketResult
from opstriage_ticketing.domain.errors import NoOpenTicket
from opstriage_ticketing.ports import StoredTicket, TicketStore


class TicketService:
    def __init__(self, store: TicketStore, public_base_url: str) -> None:
        self._store, self._base = store, public_base_url.rstrip("/")

    async def create(self, request: TicketRequest) -> TicketResult:
        ticket, created = await self._store.create_if_absent(request)
        return self._result(ticket, duplicate=not created)

    async def resolve(self, problem_id: str, resolution: str) -> TicketResult:
        ticket = await self._store.resolve_open(problem_id, resolution)
        if ticket is None:
            raise NoOpenTicket(f"no open ticket for problem {problem_id}")
        return self._result(ticket)

    def _result(self, ticket: StoredTicket, *, duplicate: bool = False) -> TicketResult:
        return TicketResult(
            ticket_id=ticket.ticket_id,
            url=f"{self._base}/tickets/{ticket.ticket_id}",
            status=ticket.status,
            duplicate=duplicate,
        )
