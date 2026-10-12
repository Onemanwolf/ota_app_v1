from collections.abc import Iterator

import pytest

from opstriage_contracts.tools import TicketRequest
from opstriage_ticketing.adapters.stores import InMemoryTicketStore
from opstriage_ticketing.application.tickets import TicketService
from opstriage_ticketing.domain.errors import NoOpenTicket


@pytest.fixture
def service() -> Iterator[TicketService]:
    yield TicketService(InMemoryTicketStore(), "http://localhost:8102")


def ticket(key: str = "run-1:ticket", problem: str = "p-1") -> TicketRequest:
    return TicketRequest(idempotency_key=key, problem_id=problem, title="t", description="d")


async def test_one_open_ticket_per_problem(service: TicketService) -> None:
    first = await service.create(ticket())
    second = await service.create(ticket(key="run-2:ticket"))
    assert first.ticket_id == second.ticket_id and second.duplicate and not first.duplicate
    assert first.url == f"http://localhost:8102/tickets/{first.ticket_id}"


async def test_resolve_by_problem_then_new_ticket_can_open(service: TicketService) -> None:
    first = await service.create(ticket())
    resolved = await service.resolve("p-1", "problem closed")
    assert resolved.ticket_id == first.ticket_id and resolved.status == "resolved"
    reopened = await service.create(ticket(key="run-3:ticket"))
    assert reopened.ticket_id != first.ticket_id


async def test_resolve_without_open_ticket(service: TicketService) -> None:
    with pytest.raises(NoOpenTicket):
        await service.resolve("p-unknown", "x")
