from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_intake.adapters.inbox_pg import PostgresInboxRepository
from opstriage_intake.domain.alert import AlertState
from opstriage_shared.db import Pool, migrate, open_pool
from opstriage_shared.settings import DatabaseSettings

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "opstriage_intake" / "migrations"
NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    with PostgresContainer("postgres:17", driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture
async def pool(pg: str) -> AsyncIterator[Pool]:
    p = await open_pool(DatabaseSettings(dsn=SecretStr(pg)))
    await migrate(p, "intake", MIGRATIONS)
    async with p.connection() as conn:
        await conn.execute("TRUNCATE intake.alert_inbox")
    yield p
    await p.close()


def _event(problem_id: str = "p-1", display_id: str | None = "P-1", status: str = "ACTIVE") -> ProblemEvent:
    return ProblemEvent(
        source=AlertSource.SIMULATOR,
        problem_id=problem_id,
        display_id=display_id,
        status=ProblemStatus(status),
        title="t",
        started_at=NOW,
        observed_at=NOW,
    )


async def test_insert_dedup_and_read_back(pool: Pool) -> None:
    repo = PostgresInboxRepository(pool)
    alert_id = uuid4()
    assert await repo.add_if_new(alert_id=alert_id, event=_event(), raw={"a": 1})
    assert not await repo.add_if_new(alert_id=uuid4(), event=_event(), raw=None)
    view = await repo.get(alert_id)
    assert view is not None and view.state is AlertState.PENDING and view.display_id == "P-1"
    await repo.notify_new()


async def test_secondary_dedup_on_display_id(pool: Pool) -> None:
    """Webhook and poller may disagree on problem_id; the display_id:status key still deduplicates."""
    repo = PostgresInboxRepository(pool)
    assert await repo.add_if_new(alert_id=uuid4(), event=_event("pid-from-webhook"), raw=None)
    assert not await repo.add_if_new(alert_id=uuid4(), event=_event("pid-from-poller"), raw=None)
    assert await repo.add_if_new(alert_id=uuid4(), event=_event("pid-from-poller", status="CLOSED"), raw=None)


async def test_events_without_display_id_dedup_on_problem_id_only(pool: Pool) -> None:
    repo = PostgresInboxRepository(pool)
    assert await repo.add_if_new(alert_id=uuid4(), event=_event("a", None), raw=None)
    assert await repo.add_if_new(alert_id=uuid4(), event=_event("b", None), raw=None)
