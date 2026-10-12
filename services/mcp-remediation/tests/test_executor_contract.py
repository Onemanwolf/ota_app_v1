"""Executor contract suite (spec 15): every RestartExecutor adapter passes the same tests (LSP).

The local_docker variant restarts the compose stack's `demo-orders-api`: run with `uv run pytest -m compose`.
"""

from collections.abc import AsyncIterator

import pytest

from opstriage_remediation.adapters.fake_executor import FakeExecutor
from opstriage_remediation.domain.targets import RestartTarget
from opstriage_remediation.ports.executor import RestartExecutor


def make_target(container: str) -> RestartTarget:
    return RestartTarget.model_validate(
        {
            "id": "contract-target",
            "description": "contract test",
            "match": {"entity_ids": ["X"]},
            "executor": {"kind": "local_docker", "container": container},
            "policy": {"autonomy": "approval_required", "restart_timeout_seconds": 5},
        }
    )


@pytest.fixture(params=["fake", pytest.param("local_docker", marks=pytest.mark.compose)])
async def executor(request: pytest.FixtureRequest) -> AsyncIterator[RestartExecutor]:
    if request.param == "fake":
        yield FakeExecutor({"demo-orders-api"})
        return
    from opstriage_remediation.adapters.local_docker import LocalDockerExecutor

    yield LocalDockerExecutor.from_env()


async def test_restart_changes_start_time(executor: RestartExecutor) -> None:
    target = make_target("demo-orders-api")
    before = await executor.status(target)
    outcome = await executor.restart(target, timeout_s=5)
    after = await executor.status(target)
    assert outcome.succeeded and "exit_code=0" in (outcome.output_tail or "")
    assert after.running and before.started_at and after.started_at and after.started_at > before.started_at


async def test_unknown_container_fails_cleanly(executor: RestartExecutor) -> None:
    target = make_target("does-not-exist")
    outcome = await executor.restart(target, timeout_s=5)
    assert not outcome.succeeded
    assert not (await executor.status(target)).running
