import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_shared.db import Pool, connect, migrate, open_pool
from opstriage_shared.settings import DatabaseSettings
from opstriage_triage.adapters.approvals_pg import PostgresApprovalStore
from opstriage_triage.adapters.inbox_pg import PostgresInbox
from opstriage_triage.adapters.runs_pg import PostgresRunRepository

pytestmark = pytest.mark.integration
SERVICES = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    with PostgresContainer("postgres:17", driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture
async def pool(pg: str) -> AsyncIterator[Pool]:
    p = await open_pool(DatabaseSettings(dsn=SecretStr(pg), pool_max_size=12))
    await migrate(p, "intake", SERVICES / "intake-api" / "src" / "opstriage_intake" / "migrations")
    await migrate(p, "triage", SERVICES / "agent-runtime" / "src" / "opstriage_triage" / "migrations")
    async with p.connection() as conn:
        await conn.execute(
            "TRUNCATE intake.alert_inbox, triage.approval_requests, triage.triage_runs, triage.audit_log"
        )
    yield p
    await p.close()


def event(pid: str, status: str = "ACTIVE") -> ProblemEvent:
    return ProblemEvent(
        source=AlertSource.SIMULATOR,
        problem_id=pid,
        status=ProblemStatus(status),
        title="t",
        started_at=NOW,
        observed_at=NOW,
    )


async def add(pool: Pool, e: ProblemEvent) -> UUID:
    alert_id = uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO intake.alert_inbox (alert_id, dedup_key, source, problem_id, status, event) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                alert_id,
                e.dedup_key,
                e.source.value,
                e.problem_id,
                e.status.value,
                Jsonb(e.model_dump(mode="json")),
            ),
        )
    return alert_id


def inbox(pool: Pool, pg: str, lease: int = 900, max_attempts: int = 3, backoff: float = 0) -> PostgresInbox:
    settings = DatabaseSettings(dsn=SecretStr(pg))
    return PostgresInbox(
        pool,
        lambda: connect(settings),
        lease_seconds=lease,
        max_attempts=max_attempts,
        retry_backoff_seconds=backoff,
    )


async def test_ten_concurrent_claimers_never_share_a_row(pool: Pool, pg: str) -> None:
    ids = {await add(pool, event(f"p-{i}")) for i in range(25)}
    box = inbox(pool, pg)

    async def drain() -> list[UUID]:
        got = []
        while (alert := await box.claim_next()) is not None:
            got.append(alert.alert_id)
        return got

    claimed = [a for batch in await asyncio.gather(*(drain() for _ in range(10))) for a in batch]
    assert sorted(claimed) == sorted(ids)  # every row once, none twice


async def test_expired_lease_is_reclaimed_then_fails_after_max_attempts(pool: Pool, pg: str) -> None:
    alert_id = await add(pool, event("p-lease"))
    box = inbox(pool, pg, lease=1, max_attempts=2)
    assert (await box.claim_next()) is not None
    assert await box.claim_next() is None  # leased
    async with pool.connection() as conn:
        await conn.execute("UPDATE intake.alert_inbox SET locked_until = now() - interval '1 second'")
    second = await box.claim_next()
    assert second is not None and second.attempts == 2
    async with pool.connection() as conn:
        await conn.execute("UPDATE intake.alert_inbox SET locked_until = now() - interval '1 second'")
    assert await box.claim_next() is None  # attempts exhausted
    assert await box.sweep_expired() == [alert_id]


async def test_fail_returns_to_pending_until_max_attempts(pool: Pool, pg: str) -> None:
    alert_id = await add(pool, event("p-fail"))
    box = inbox(pool, pg, max_attempts=2)
    await box.claim_next()
    assert await box.fail(alert_id, "Boom") == "pending"
    await box.claim_next()
    assert await box.fail(alert_id, "Boom") == "failed"


async def test_failed_attempt_waits_out_the_backoff(pool: Pool, pg: str) -> None:
    alert_id = await add(pool, event("p-backoff"))
    box = inbox(pool, pg, backoff=60)
    await box.claim_next()
    assert await box.fail(alert_id, "Boom") == "pending"
    assert await box.claim_next() is None  # still backing off
    async with pool.connection() as conn:
        await conn.execute("UPDATE intake.alert_inbox SET locked_until = now() - interval '1 second'")
    retried = await box.claim_next()
    assert retried is not None and retried.attempts == 2


async def test_supersede_and_latest_event(pool: Pool, pg: str) -> None:
    active = await add(pool, event("p-sup"))
    closed = await add(pool, event("p-sup", "CLOSED"))
    box = inbox(pool, pg)
    assert await box.supersede_pending("p-sup", except_alert_id=closed) == 1
    latest = await box.latest_event("p-sup")
    assert latest is not None and latest.status is ProblemStatus.CLOSED
    assert (await box.get_event(active)) is not None


async def test_notify_wakes_a_listener(pool: Pool, pg: str) -> None:
    box = inbox(pool, pg)
    waiter = asyncio.create_task(box.wait_for_notify(max_wait=10))
    await asyncio.sleep(0.5)
    async with pool.connection() as conn:
        await conn.execute("NOTIFY alert_inbox")
    await asyncio.wait_for(waiter, timeout=5)
    await box.close()


async def test_approval_lifecycle(pool: Pool) -> None:
    store = PostgresApprovalStore(pool, "manual_api")
    first = await store.open(run_id="r-1", problem_id="p", target_id="t")
    again = await store.open(run_id="r-1", problem_id="p", target_id="t")
    assert first.approval_id == again.approval_id and first.state == "requested"
    decided = await store.decide("r-1", approved=True, decided_by="tim", reason=None)
    assert decided is not None and decided.state == "approved"
    assert await store.decide("r-1", approved=False, decided_by="x", reason=None) is None  # only once
    assert [r.run_id for r in await store.decided_not_resumed()] == ["r-1"]
    await store.mark_resumed("r-1")
    assert await store.decided_not_resumed() == []

    await store.open(run_id="r-2", problem_id="p2", target_id="t")
    await store.open(run_id="r-3", problem_id="p3", target_id="t")
    assert await store.cancel_for_problem("p2", "problem_closed") == ["r-2"]
    assert await store.expire_older_than(datetime.now(UTC) + timedelta(seconds=1)) == ["r-3"]


async def test_runs_and_audit(pool: Pool) -> None:
    runs = PostgresRunRepository(pool)
    summary = {
        "run_id": "r-9",
        "problem_id": "p",
        "display_id": "P-1",
        "event_status": "ACTIVE",
        "decision": {"action": "notify_only"},
        "decision_error": None,
        "target": None,
        "plan": ["notify"],
        "approval": None,
        "outcomes": [{"step": "notify", "ok": True}],
    }
    await runs.save_run(summary)
    await runs.save_run(summary | {"plan": ["notify", "ticket"]})  # upsert
    await runs.append_audit("r-9", "decision", {"action": "notify_only"})
    stored = await runs.get_run("r-9")
    assert (
        stored is not None
        and stored["plan"] == ["notify", "ticket"]
        and stored["audit"][0]["kind"] == "decision"
    )
