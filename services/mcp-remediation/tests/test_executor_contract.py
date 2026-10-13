"""Executor contract suite (spec 15): every RestartExecutor adapter passes the same tests (LSP).

- `fake`: in memory.
- `azure_run_command`: against a scripted ARM endpoint (httpx2.MockTransport) and StaticCredentials. The manual
  stage against one Azure test VM (spec 12.5.5) is not automated.
- `local_docker`: restarts the compose stack's `demo-orders-api`: run with `uv run pytest -m compose`.

Status: an executor either reports container state or raises ExecutorUnavailable (Run Command cannot observe
state without another root-level command; the use case reports that as "unavailable").
"""

from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace

import pytest

from opstriage_remediation.adapters.fake_executor import FakeExecutor
from opstriage_remediation.domain.targets import RestartTarget
from opstriage_remediation.ports.executor import ContainerStatus, ExecutorUnavailable, RestartExecutor


def make_target(container: str, kind: str = "local_docker", resource_id: str | None = None) -> RestartTarget:
    return RestartTarget.model_validate(
        {
            "id": "contract-target",
            "description": "contract test",
            "match": {"entity_ids": ["X"]},
            "executor": {"kind": kind, "container": container, "resource_id": resource_id},
            "policy": {"autonomy": "approval_required", "restart_timeout_seconds": 5},
        }
    )


@pytest.fixture(params=["fake", "azure_run_command", pytest.param("local_docker", marks=pytest.mark.compose)])
async def executor(
    request: pytest.FixtureRequest, arm_kit: SimpleNamespace
) -> AsyncIterator[RestartExecutor]:
    if request.param == "fake":
        yield FakeExecutor({"demo-orders-api"})
        return
    if request.param == "azure_run_command":
        azure, _ = arm_kit.make_executor(arm_kit.FakeArm(allowed={"demo-orders-api"}))
        yield azure
        await azure.close()
        return
    from opstriage_remediation.adapters.local_docker import LocalDockerExecutor

    yield LocalDockerExecutor.from_env()


@pytest.fixture
def target_for(executor: RestartExecutor, arm_kit: SimpleNamespace) -> Callable[[str], RestartTarget]:
    def build(container: str) -> RestartTarget:
        if executor.kind == "azure_run_command":
            return make_target(container, "azure_run_command", arm_kit.VM_ID)
        return make_target(container)

    return build


async def _status(executor: RestartExecutor, target: RestartTarget) -> ContainerStatus | None:
    try:
        return await executor.status(target)
    except ExecutorUnavailable:
        return None  # allowed by the contract: state not observable through this executor


async def test_restart_succeeds_and_reports_exit_code(
    executor: RestartExecutor, target_for: Callable[[str], RestartTarget]
) -> None:
    target = target_for("demo-orders-api")
    before = await _status(executor, target)
    outcome = await executor.restart(target, timeout_s=5)
    after = await _status(executor, target)
    assert outcome.succeeded and "exit_code=0" in (outcome.output_tail or "")
    if before is not None and after is not None:  # observable executors: the start time moved
        assert (
            after.running and before.started_at and after.started_at and after.started_at > before.started_at
        )


async def test_unknown_container_fails_cleanly(
    executor: RestartExecutor, target_for: Callable[[str], RestartTarget]
) -> None:
    target = target_for("does-not-exist")
    outcome = await executor.restart(target, timeout_s=5)
    assert not outcome.succeeded
    status = await _status(executor, target)
    assert status is None or not status.running
