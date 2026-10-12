from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from opstriage_contracts.problems import EntityRef
from opstriage_contracts.tools import ApprovalProof, RemediationStatus, ResolveTargetRequest, RestartRequest
from opstriage_remediation.adapters.memory_store import InMemoryExecutionStore
from opstriage_remediation.application.service import RemediationService
from opstriage_remediation.domain.errors import PolicyViolation

T0 = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
APPROVAL = ApprovalProof(approval_id="a-1", approved_by="system:auto", approved_at=T0, mode="auto_approve")


def req(key: str = "run-1:restart", target: str = "demo-orders-api", **kw: object) -> RestartRequest:
    return RestartRequest.model_validate(
        {
            "target_id": target,
            "idempotency_key": key,
            "problem_id": "p-1",
            "reason": "test",
            "approval": APPROVAL,
        }
        | kw
    )


async def test_approved_request_is_recorded_as_dry_run(
    service: RemediationService, store: InMemoryExecutionStore
) -> None:
    result = await service.restart_container(req())
    assert result.status is RemediationStatus.DRY_RUN and not result.duplicate
    assert store.executions["run-1:restart"].approved_by == "system:auto"


async def test_same_key_returns_stored_result(service: RemediationService) -> None:
    await service.restart_container(req())
    again = await service.restart_container(req())
    assert again.duplicate and again.status is RemediationStatus.DRY_RUN


async def test_kill_switch_rejects_and_allows_retry_later(
    service_factory: Callable[..., RemediationService], store: InMemoryExecutionStore
) -> None:
    off = service_factory(enabled=False)
    result = await off.restart_container(req())
    assert result.status is RemediationStatus.REJECTED and result.reason == "disabled"
    assert store.executions == {} and store.rejections[0].reason == "disabled"
    on = service_factory()
    assert (await on.restart_container(req())).status is RemediationStatus.DRY_RUN  # same key, now allowed


async def test_unknown_target_is_a_policy_violation(service: RemediationService) -> None:
    with pytest.raises(PolicyViolation):
        await service.restart_container(req(target="not-registered"))


async def test_notify_only_target_is_rejected(service: RemediationService) -> None:
    result = await service.restart_container(req(target="demo-orders-worker"))
    assert result.status is RemediationStatus.REJECTED and "notify_only" in (result.reason or "")


async def test_global_ceiling_lowers_target_autonomy(
    service_factory: Callable[..., RemediationService],
) -> None:
    svc = service_factory(max_autonomy="notify_only")
    assert (await svc.restart_container(req())).status is RemediationStatus.REJECTED


async def test_missing_expired_or_disallowed_approval_is_rejected(
    service_factory: Callable[..., RemediationService], clock: Any
) -> None:
    svc = service_factory(allowed_approval_modes={"manual_api"})
    assert (await svc.restart_container(req("run-1:restart", approval=None))).reason == "approval required"
    assert "not allowed" in ((await svc.restart_container(req("run-2:restart"))).reason or "")
    clock.current = T0 + timedelta(minutes=31)
    manual = APPROVAL.model_copy(update={"mode": "manual_api"})
    assert (await svc.restart_container(req("run-3:restart", approval=manual))).reason == "approval expired"


async def test_resolve_target_reports_ambiguity(service: RemediationService) -> None:
    api = EntityRef(id="CONTAINER-8A1F00C2D3E4B5A6", type="container", name="demo-orders-api")
    worker = EntityRef(id="CONTAINER-9B2E11D3E4F5A6B7", type="container", name="demo-orders-worker")
    single = await service.resolve_target(ResolveTargetRequest(problem_id="p", entities=(api,)))
    assert single.target is not None and single.target.autonomy == "approval_required"
    both = await service.resolve_target(ResolveTargetRequest(problem_id="p", entities=(api, worker)))
    assert both.target is None and both.ambiguous


async def test_dry_runs_do_not_count_as_recent_restarts(service: RemediationService) -> None:
    await service.restart_container(req())
    api = EntityRef(id="CONTAINER-8A1F00C2D3E4B5A6", type="container")
    result = await service.resolve_target(ResolveTargetRequest(problem_id="p", entities=(api,)))
    assert result.recent_restarts_24h == 0
