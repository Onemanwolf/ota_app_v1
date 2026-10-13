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


async def test_postgres_store_completion_expiry_and_listing(pool: Pool) -> None:
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from opstriage_insights.adapters.stores import PostgresReviewStore

    await migrate(pool, "insights", MIGRATIONS)
    store, t0 = PostgresReviewStore(pool), datetime(2026, 10, 12, 6, tzinfo=UTC)
    rid = UUID(int=77)
    await store.start_review(rid, "review", "c-pg", None, t0, t0 + timedelta(hours=28))
    await store.save_evidence(rid, "logs", t0, t0 + timedelta(hours=28), {"records": []})
    assert (await store.get_report(rid, t0)).report is None  # type: ignore[union-attr]
    done = t0 + timedelta(minutes=4)
    report = {"review_id": str(rid), "worst_risk": "high", "counts": {"by_risk": {"high": 1}}}
    await store.complete_review(
        rid,
        completed_at=done,
        review_expires_at=done + timedelta(hours=48),
        evidence_expires_at=done + timedelta(hours=4),
        report=report,
        markdown="# r",
        dropped_uncited=2,
        guard_withheld=1,
    )
    stored = await store.get_report(rid, done)
    assert stored is not None and stored.report == report and stored.markdown == "# r" and not stored.expired
    [row] = await store.list_reports(done, "c-pg")
    assert row.status == "completed" and row.worst_risk == "high" and row.by_risk == {"high": 1}
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT expires_at FROM insights.evidence WHERE review_id = %s", (rid,))
        assert (await cur.fetchone())["expires_at"] == done + timedelta(hours=4)  # type: ignore[index]
    expired = await store.get_report(rid, done + timedelta(hours=49))
    assert expired is not None and expired.expired and expired.report is None
    assert await store.list_reports(done + timedelta(hours=49), "c-pg") == []
    await store.fail_review(rid, "x")  # completed reviews are not overwritten
    assert (await store.get_review(rid)).status == "completed"  # type: ignore[union-attr]
