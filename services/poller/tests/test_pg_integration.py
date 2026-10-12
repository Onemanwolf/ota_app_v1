import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from opstriage_poller.adapters.postgres import PostgresCursorStore, PostgresLeaderLock
from opstriage_shared.db import Pool, migrate, open_pool
from opstriage_shared.settings import DatabaseSettings

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "opstriage_poller" / "migrations"
T0 = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    with PostgresContainer("postgres:17", driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture
async def pool(pg: str) -> AsyncIterator[Pool]:
    p = await open_pool(DatabaseSettings(dsn=SecretStr(pg), pool_max_size=6))
    await migrate(p, "poller", MIGRATIONS)
    yield p
    await p.close()


async def test_cursor_round_trip_and_never_moves_backwards(pool: Pool) -> None:
    store = PostgresCursorStore(pool)
    assert await store.get("grail") is None
    await store.set("grail", high_water_mark=T0, last_sweep_at=None)
    await store.set("grail", high_water_mark=T0 - timedelta(hours=1), last_sweep_at=T0)
    cursor = await store.get("grail")
    assert cursor is not None and cursor.high_water_mark == T0 and cursor.last_sweep_at == T0


async def test_two_replicas_never_run_a_cycle_at_the_same_time(pool: Pool, pg: str) -> None:
    other = await open_pool(DatabaseSettings(dsn=SecretStr(pg), pool_max_size=2))  # a second "replica"
    a, b = PostgresLeaderLock(pool), PostgresLeaderLock(other)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> bool:
        async with a.try_acquire() as got:
            entered.set()
            await release.wait()
            return got

    holder = asyncio.create_task(hold())
    await entered.wait()
    async with b.try_acquire() as got_b:
        assert got_b is False
    release.set()
    assert await holder is True
    async with b.try_acquire() as got_after:
        assert got_after is True  # released after the first cycle
    await other.close()
