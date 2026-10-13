from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from opstriage_shared.db import Pool, migrate, open_pool
from opstriage_shared.settings import DatabaseSettings

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "opstriage_insights" / "migrations"


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    with PostgresContainer("postgres:17", driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture
async def pool(pg: str) -> AsyncIterator[Pool]:
    p = await open_pool(DatabaseSettings(dsn=SecretStr(pg), pool_max_size=2))
    yield p
    await p.close()


async def test_migration_creates_the_insights_tables_once(pool: Pool) -> None:
    assert await migrate(pool, "insights", MIGRATIONS) == ["001_insights.sql"]
    assert await migrate(pool, "insights", MIGRATIONS) == []
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'insights' ORDER BY 1"
        )
        tables = [r["table_name"] for r in await cur.fetchall()]
    assert tables == ["evidence", "purge_log", "reports", "reviews", "schema_migrations"]


async def test_deleting_a_review_cascades_to_evidence_and_report(pool: Pool) -> None:
    await migrate(pool, "insights", MIGRATIONS)
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO insights.reviews (id, kind, cluster, expires_at) "
            "VALUES ('00000000-0000-0000-0000-000000000001', 'review', 'c1', now() + interval '28 hours')"
        )
        await conn.execute(
            "INSERT INTO insights.evidence (review_id, source, collected_at, expires_at, bundle) "
            "VALUES ('00000000-0000-0000-0000-000000000001', 'logs', now(), now() + interval '28 hours', '{}')"
        )
        await conn.execute(
            "INSERT INTO insights.reports (review_id, completed_at, expires_at, report, markdown) "
            "VALUES ('00000000-0000-0000-0000-000000000001', now(), now() + interval '48 hours', '{}', '')"
        )
        await conn.execute("DELETE FROM insights.reviews WHERE cluster = 'c1'")
        cur = await conn.execute(
            "SELECT (SELECT count(*) FROM insights.evidence) AS e, (SELECT count(*) FROM insights.reports) AS r"
        )
        row = await cur.fetchone()
    assert row is not None and (row["e"], row["r"]) == (0, 0)
