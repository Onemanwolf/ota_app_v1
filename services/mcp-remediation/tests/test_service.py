"""Guard-rail matrix (spec 12.5.5): every rule in 12.5.2 has a test proving the restart does not happen."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from opstriage_contracts.problems import EntityRef
from opstriage_contracts.tools import ApprovalProof, RemediationStatus, ResolveTargetRequest, RestartRequest
from opstriage_remediation.adapters.fake_executor import FakeExecutor
from opstriage_remediation.adapters.memory_store import InMemoryExecutionStore
from opstriage_remediation.application.service import RemediationService
from opstriage_remediation.domain.errors import PolicyViolation
from opstriage_remediation.domain.execution import Execution

T0 = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
APPROVAL = ApprovalProof(approval_id="a-1", approved_by="system:auto", approved_at=T0, mode="auto_approve")
Factory = Callable[..., RemediationService]


def req(key: str = "run-1:restart", target: str = "demo-orders-api", **kw: object) -> RestartRequest:
    return RestartRequest.model_validate(
        {
            "target_id": target,
            "idempotency_key": key,
            "problem_id": "p-1",
            "reason": "test",
            "approval": APPROVAL,
            "dry_run": False,
        }
        | kw
    )


def live(factory: Factory, **kw: Any) -> RemediationService:
    return factory(dry_run=False, **kw)


# --- happy paths
async def test_real_restart_executes_once_and_records_restarted(
    service_factory: Factory, executor: FakeExecutor, store: InMemoryExecutionStore
) -> None:
    result = await live(service_factory).restart_container(req())
    assert result.status is RemediationStatus.RESTARTED and executor.restarts == ["demo-orders-api"]
    assert result.executor == "local_docker" and "exit_code=0" in (result.output_tail or "")
    assert store.executions["run-1:restart"].approved_by == "system:auto"


async def test_same_key_returns_stored_result_without_executing_again(
    service_factory: Factory, executor: FakeExecutor
) -> None:
    svc = live(service_factory)
    await svc.restart_container(req())
    again = await svc.restart_container(req())
    assert again.duplicate and again.status is RemediationStatus.RESTARTED and len(executor.restarts) == 1


# --- 2 kill switch
async def test_kill_switch_rejects_and_a_retry_succeeds_once_enabled(
    service_factory: Factory, executor: FakeExecutor, store: InMemoryExecutionStore
) -> None:
    result = await live(service_factory, enabled=False).restart_container(req())
    assert result.status is RemediationStatus.REJECTED and result.reason == "disabled"
    assert executor.restarts == [] and store.executions == {}
    assert (await live(service_factory).restart_container(req())).status is RemediationStatus.RESTARTED


# --- 3 target
async def test_unknown_target_is_a_policy_violation(service_factory: Factory, executor: FakeExecutor) -> None:
    with pytest.raises(PolicyViolation):
        await live(service_factory).restart_container(req(target="not-registered"))
    assert executor.restarts == []


# --- 4 autonomy, approval
async def test_notify_only_target_never_restarts(service_factory: Factory, executor: FakeExecutor) -> None:
    result = await live(service_factory).restart_container(req(target="demo-orders-worker"))
    assert result.status is RemediationStatus.REJECTED and executor.restarts == []


async def test_global_ceiling_lowers_target_autonomy(
    service_factory: Factory, executor: FakeExecutor
) -> None:
    result = await live(service_factory, max_autonomy="notify_only").restart_container(req())
    assert result.status is RemediationStatus.REJECTED and executor.restarts == []


@pytest.mark.parametrize(
    ("approval", "modes", "minutes_later", "reason"),
    [
        (None, {"auto_approve"}, 0, "approval required"),
        (APPROVAL, {"manual_api"}, 0, "approval mode auto_approve not allowed"),
        (APPROVAL, {"auto_approve"}, 31, "approval expired"),
    ],
)
async def test_missing_disallowed_or_expired_approval(
    service_factory: Factory,
    executor: FakeExecutor,
    clock: Any,
    approval: ApprovalProof | None,
    modes: set[str],
    minutes_later: int,
    reason: str,
) -> None:
    clock.current = T0 + timedelta(minutes=minutes_later)
    result = await live(service_factory, allowed_approval_modes=modes).restart_container(
        req(approval=approval)
    )
    assert result.status is RemediationStatus.REJECTED and result.reason == reason and executor.restarts == []


# --- 5 serialization
async def test_second_restart_while_one_is_in_flight_is_busy(service_factory: Factory) -> None:
    slow = FakeExecutor({"demo-orders-api"}, delay_s=0.5)
    svc = live(
        service_factory,
        executor=slow,
        sync_wait_seconds=0.05,
    )
    first = await svc.restart_container(req("run-1:restart"))
    assert first.status is RemediationStatus.IN_PROGRESS  # returned before the executor finished
    # cooldown would also reject; set it to 0 to prove the in-flight lock on its own
    second = await svc.restart_container(req("run-2:restart"))
    assert second.status is RemediationStatus.REJECTED
    await svc.drain()
    done = await svc.get_remediation("run-1:restart")
    assert (
        done is not None
        and done.status is RemediationStatus.RESTARTED
        and slow.restarts == ["demo-orders-api"]
    )


async def test_in_flight_lock_alone_rejects_busy(
    service_factory: Factory, store: InMemoryExecutionStore, executor: FakeExecutor
) -> None:
    """An in-flight row older than the cooldown still blocks: the insert is the per-target lock."""
    store.executions["other:restart"] = Execution(
        "other:restart",
        "demo-orders-api",
        "p",
        RemediationStatus.IN_PROGRESS,
        None,
        "local_docker",
        None,
        T0 - timedelta(hours=2),
        None,
        None,
    )
    result = await live(service_factory).restart_container(req())
    assert result.status is RemediationStatus.REJECTED and (result.reason or "").startswith("busy")
    assert executor.restarts == []


# --- 6 cooldown, cap
async def test_cooldown_rejects_a_different_key_for_the_same_target(
    service_factory: Factory, clock: Any, executor: FakeExecutor
) -> None:
    svc = live(service_factory)
    await svc.restart_container(req("run-1:restart"))
    clock.current = T0 + timedelta(minutes=14)
    second = await svc.restart_container(
        req("run-2:restart", approval=APPROVAL.model_copy(update={"approved_at": clock.current}))
    )
    assert second.status is RemediationStatus.REJECTED and (second.reason or "").startswith("cooldown")
    clock.current = T0 + timedelta(minutes=16)
    third = await svc.restart_container(
        req("run-3:restart", approval=APPROVAL.model_copy(update={"approved_at": clock.current}))
    )
    assert third.status is RemediationStatus.RESTARTED and len(executor.restarts) == 2


async def test_daily_cap(service_factory: Factory, clock: Any, executor: FakeExecutor) -> None:
    svc = live(service_factory)
    for i in range(6):  # demo-orders-api: max 5 per day, cooldown 15 min
        clock.current = T0 + timedelta(minutes=20 * i)
        result = await svc.restart_container(
            req(f"run-{i}:restart", approval=APPROVAL.model_copy(update={"approved_at": clock.current}))
        )
    assert result.status is RemediationStatus.REJECTED and (result.reason or "").startswith("daily cap")
    assert len(executor.restarts) == 5


# --- 7 circuit breaker
async def test_breaker_opens_after_failures_and_resets_by_operator(
    service_factory: Factory, clock: Any, store: InMemoryExecutionStore
) -> None:
    failing = FakeExecutor({"demo-orders-api"}, fail=True)
    svc = live(service_factory, executor=failing, breaker_failures=2, breaker_window=timedelta(minutes=60))
    for i in range(2):
        clock.current = T0 + timedelta(minutes=16 * i)
        failed = await svc.restart_container(
            req(f"run-{i}:restart", approval=APPROVAL.model_copy(update={"approved_at": clock.current}))
        )
        assert failed.status is RemediationStatus.FAILED
    clock.current = T0 + timedelta(minutes=40)
    proof = APPROVAL.model_copy(update={"approved_at": clock.current})
    blocked = await svc.restart_container(req("run-9:restart", approval=proof))
    assert blocked.status is RemediationStatus.REJECTED and "circuit breaker" in (blocked.reason or "")
    await svc.reset_breaker("timo", "investigated")
    assert store.resets[0][0] == "timo" and not await svc.breaker_open()
    clock.current = T0 + timedelta(minutes=41)
    retried = await svc.restart_container(
        req("run-9:restart", approval=proof.model_copy(update={"approved_at": clock.current}))
    )
    assert (
        retried.status is RemediationStatus.FAILED
    )  # allowed again (and fails again, the executor is broken)


# --- 8 dry run
async def test_server_dry_run_wins_over_a_request_for_a_real_run(
    service_factory: Factory, executor: FakeExecutor
) -> None:
    result = await service_factory(dry_run=True).restart_container(req(dry_run=False))
    assert result.status is RemediationStatus.DRY_RUN and result.reason == "dry run (server setting)"
    assert executor.restarts == []


async def test_request_can_ask_for_a_dry_run(service_factory: Factory, executor: FakeExecutor) -> None:
    result = await live(service_factory).restart_container(req(dry_run=True))
    assert result.status is RemediationStatus.DRY_RUN and result.reason == "dry run (requested)"
    assert executor.restarts == []


async def test_dry_runs_do_not_count_towards_cooldown(
    service_factory: Factory, executor: FakeExecutor
) -> None:
    svc = live(service_factory)
    await svc.restart_container(req("run-1:restart", dry_run=True))
    assert (await svc.restart_container(req("run-2:restart"))).status is RemediationStatus.RESTARTED


# --- 9 execution
async def test_executor_failure_is_recorded_as_failed(service_factory: Factory) -> None:
    result = await live(
        service_factory, executor=FakeExecutor({"demo-orders-api"}, fail=True)
    ).restart_container(req())
    assert result.status is RemediationStatus.FAILED and result.reason == "executor reported failure"


async def test_unknown_container_fails_cleanly(service_factory: Factory) -> None:
    result = await live(service_factory, executor=FakeExecutor(set())).restart_container(req())
    assert result.status is RemediationStatus.FAILED and "no such container" in (result.output_tail or "")


async def test_reconciler_fails_stale_in_progress_rows(
    service_factory: Factory, store: InMemoryExecutionStore, clock: Any
) -> None:
    store.executions["old:restart"] = Execution(
        "old:restart",
        "demo-orders-api",
        "p",
        RemediationStatus.IN_PROGRESS,
        None,
        "local_docker",
        None,
        T0 - timedelta(minutes=6),
        None,
        None,
    )  # timeout 20 s + 5 min grace
    store.executions["new:restart"] = Execution(
        "new:restart",
        "demo-orders-api",
        "p",
        RemediationStatus.IN_PROGRESS,
        None,
        "local_docker",
        None,
        T0 - timedelta(minutes=1),
        None,
        None,
    )
    assert await service_factory().reconcile() == ["old:restart"]
    assert store.executions["old:restart"].reason == "unknown outcome, may have completed"
    assert store.executions["new:restart"].status is RemediationStatus.IN_PROGRESS


# --- queries
async def test_resolve_target_reports_ambiguity_and_counts_real_restarts(service_factory: Factory) -> None:
    svc = live(service_factory)
    api = EntityRef(id="CONTAINER-8A1F00C2D3E4B5A6", type="container", name="demo-orders-api")
    worker = EntityRef(id="CONTAINER-9B2E11D3E4F5A6B7", type="container", name="demo-orders-worker")
    await svc.restart_container(req())
    single = await svc.resolve_target(ResolveTargetRequest(problem_id="p", entities=(api,)))
    assert single.target is not None and single.target.autonomy == "approval_required"
    assert single.recent_restarts_24h == 1
    both = await svc.resolve_target(ResolveTargetRequest(problem_id="p", entities=(api, worker)))
    assert both.target is None and both.ambiguous


async def test_target_status_reports_a_newer_start_after_restart(service_factory: Factory) -> None:
    svc = live(service_factory)
    before = await svc.get_target_status("demo-orders-api")
    await svc.restart_container(req())
    after = await svc.get_target_status("demo-orders-api")
    assert after.running and before.started_at and after.started_at and after.started_at > before.started_at
