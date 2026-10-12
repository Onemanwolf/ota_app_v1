from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from opstriage_contracts.tools import TicketRequest


@dataclass(frozen=True, slots=True)
class StoredTicket:
    ticket_id: str
    status: Literal["open", "resolved"]


class TicketStore(Protocol):
    async def create_if_absent(self, request: TicketRequest) -> tuple[StoredTicket, bool]:
        """(ticket, created). Returns the existing open ticket for the problem, or the ticket for the key."""
        ...

    async def resolve_open(self, problem_id: str, resolution: str) -> StoredTicket | None: ...
