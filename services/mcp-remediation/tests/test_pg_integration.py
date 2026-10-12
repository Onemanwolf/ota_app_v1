import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from opstriage_contracts.tools import RemediationStatus
from opstriage_remediation.adapters.executions_pg import PostgresExecutionStore
from opstriage_remediation.domain.execution import Execution
from opstriage_shared.db import Pool, migrate, open_pool
from opstriage_shared.settings import DatabaseSettings

pytestmark = pytest.mark.integration
MIGRATIONS = Path(__file__).resolve().parents[1] / "src" / "opstriage_remediation" / "migrations"
NOW = datetime.now(UTC)


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    with PostgresContainer("postgres:17", driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture
async def store(pg: str) -> AsyncIterator[PostgresExecutionStore]:
    pool: Pool = await open_pool(DatabaseSettings(dsn=SecretStr(pg), pool_max_size=8))
    await migrate(pool, "remediation", MIGRATIONS)
    async with pool.connection() as conn:
        await conn.execute(
            "TRUNCATE remediation.executions, remediation.rejections, remediation.breaker_resets"
        )
    yield PostgresExecutionStore(pool)
    await pool.close()


def execution(
    key: str,
    target: str = "t1",
    status: RemediationStatus = RemediationStatus.IN_PROGRESS,
    started: datetime = NOW,
) -> Execution:
    return Execution(key, target, "p", status, None, "local_docker", None, started, None, None)


async def test_start_tells_busy_from_duplicate_by_constraint(store: PostgresExecutionStore) -> None:
    assert await store.start(execution("k1:restart")) == "started"
    assert await store.start(execution("k1:restart")) == "duplicate"  # same key: primary key
    assert await store.start(execution("k2:restart")) == "busy"  # same target in flight: partial unique index
    assert await store.start(execution("k3:restart", target="t2")) == "started"  # other targets unaffected
    await store.finish(
        "k1:restart",
        status=RemediationStatus.RESTARTED,
        finished_at=NOW,
        reason=None,
        output_tail="exit_code=0",
    )
    assert await store.start(execution("k2:restart")) == "started"  # lock released once terminal


async def test_concurrent_starts_for_one_target_admit_exactly_one(store: PostgresExecutionStore) -> None:
    outcomes = await asyncio.gather(*(store.start(execution(f"c{i}:restart")) for i in range(8)))
    assert outcomes.count("started") == 1 and outcomes.count("busy") == 7


async def test_finish_only_moves_in_progress_rows(store: PostgresExecutionStore) -> None:
    await store.start(execution("f1:restart"))
    await store.finish(
        "f1:restart", status=RemediationStatus.RESTARTED, finished_at=NOW, reason=None, output_tail="ok"
    )
    await store.finish(
        "f1:restart", status=RemediationStatus.FAILED, finished_at=NOW, reason="late", output_tail=None
    )  # a late writer never overwrites a terminal state
    stored = await store.get("f1:restart")
    assert stored is not None and stored.status is RemediationStatus.RESTARTED and stored.output_tail == "ok"


async def test_counting_failures_and_breaker_resets(store: PostgresExecutionStore) -> None:
    await store.insert(execution("d:restart", status=RemediationStatus.DRY_RUN))
    await store.insert(execution("x:restart", target="t9", status=RemediationStatus.FAILED))
    assert await store.count_since("t1", NOW - timedelta(minutes=1), [RemediationStatus.IN_PROGRESS]) == 0
    assert await store.failures_since(NOW - timedelta(minutes=1)) == 1
    assert await store.last_breaker_reset() is None
    await store.reset_breaker(operator="timo", reason="test")
    reset = await store.last_breaker_reset()
    assert reset is not None and reset >= NOW - timedelta(minutes=1)


async def test_fail_stale_marks_only_old_in_progress_rows(store: PostgresExecutionStore) -> None:
    await store.start(execution("old:restart", started=NOW - timedelta(minutes=10)))
    await store.start(execution("other:restart", target="t2"))
    keys = await store.fail_stale("t1", NOW - timedelta(minutes=5), "unknown outcome, may have completed")
    assert keys == ["old:restart"]
    stored = await store.get("old:restart")
    assert stored is not None and stored.status is RemediationStatus.FAILED and stored.finished_at is not None
