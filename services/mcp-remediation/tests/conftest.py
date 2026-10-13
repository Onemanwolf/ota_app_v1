from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest

from opstriage_remediation.adapters.azure_run_command import AzureRunCommandExecutor
from opstriage_remediation.adapters.fake_executor import FakeExecutor
from opstriage_remediation.adapters.memory_store import InMemoryExecutionStore
from opstriage_remediation.application.service import GuardSettings, RemediationService
from opstriage_remediation.bootstrap import load_registry
from opstriage_remediation.domain.targets import RestartTarget, TargetRegistry
from opstriage_remediation.settings import AzureSettings
from opstriage_shared.credentials import StaticCredentials

ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current


@pytest.fixture
def registry() -> TargetRegistry:
    return load_registry(ROOT / "config" / "targets.example.yaml")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(clock: FakeClock) -> InMemoryExecutionStore:
    return InMemoryExecutionStore(clock)


@pytest.fixture
def executor() -> FakeExecutor:
    return FakeExecutor({"demo-orders-api"})


def make_service(
    registry: TargetRegistry, store: InMemoryExecutionStore, clock: FakeClock, executor: Any, **guard: object
) -> RemediationService:
    settings: dict[str, Any] = {
        "enabled": True,
        "dry_run": True,
        "max_autonomy": "approval_required",
        "approval_ttl": timedelta(minutes=30),
        "allowed_approval_modes": {"auto_approve", "manual_api"},
        "sync_wait_seconds": 5,
        "breaker_failures": 3,
        "breaker_window": timedelta(minutes=30),
    } | guard
    return RemediationService(
        registry, store, dict.fromkeys(registry.executor_kinds(), executor), clock, GuardSettings(**settings)
    )


@pytest.fixture
def service_factory(
    registry: TargetRegistry, store: InMemoryExecutionStore, clock: FakeClock, executor: FakeExecutor
) -> Callable[..., RemediationService]:
    def factory(**guard: object) -> RemediationService:
        return make_service(registry, store, clock, guard.pop("executor", executor), **guard)

    return factory


@pytest.fixture
def service(service_factory: Callable[..., RemediationService]) -> RemediationService:
    return service_factory()


# ---------- scripted ARM endpoint for VM Run Command (azure_run_command tests and the executor contract) ----------
# Shapes follow the INFERRED ARM behaviour documented in adapters/azure_run_command.py.

SUB = "00000000-1111-2222-3333-444444444444"
VM_ID = (
    f"/subscriptions/{SUB}/resourceGroups/rg-orders/providers/Microsoft.Compute/virtualMachines/vm-orders-01"
)
ARM = "https://management.azure.com"
TOKEN = "arm-secret-token-value"
OP_URL = f"{ARM}/subscriptions/{SUB}/providers/Microsoft.Compute/locations/westeurope/operations/op-1?api-version=x"
RESULT_URL = (
    f"{ARM}/subscriptions/{SUB}/providers/Microsoft.Compute/locations/westeurope/results/op-1?api-version=x"
)


def script_output(code: int, stdout: str = "") -> str:
    state = "succeeded" if code == 0 else "failed"
    return f"Enable {state}: \n[stdout]\n{stdout}exit_code={code}\n\n[stderr]\n"


@dataclass
class FakeArm:
    """POST runCommand -> 202 (+ Azure-AsyncOperation, Location); `in_progress_polls` InProgress answers; then
    Succeeded. The VM side behaves like restart-allowed.sh with `allowed` containers."""

    allowed: set[str] = field(default_factory=lambda: {"demo-orders-api"})
    in_progress_polls: int = 1
    retry_after: str | None = "2"
    post_errors: list[int] = field(default_factory=list)  # status codes answered before the 202
    poll_errors: list[int] = field(default_factory=list)
    output_in_operation: bool = True  # else the result is fetched from Location
    arm_status: str = "Succeeded"
    requests: list[httpx2.Request] = field(default_factory=list)
    _polls: int = 0
    _container: str = ""

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.method == "POST":
            if self.post_errors:
                return self._error(self.post_errors.pop(0))
            body = json.loads(request.content)
            self._container = body["parameters"][0]["value"]
            headers = {"Azure-AsyncOperation": OP_URL, "Location": RESULT_URL}
            if self.retry_after:
                headers["Retry-After"] = self.retry_after
            return httpx2.Response(202, headers=headers)
        if self.poll_errors:
            return self._error(self.poll_errors.pop(0))
        if str(request.url) == RESULT_URL:
            return httpx2.Response(200, json={"value": self._value()})
        self._polls += 1
        if self._polls <= self.in_progress_polls:
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx2.Response(200, json={"status": "InProgress"}, headers=headers)
        op: dict[str, Any] = {"status": self.arm_status, "name": "op-1"}
        if self.arm_status != "Succeeded":
            op["error"] = {"code": "VMAgentStatusCommunicationError", "message": f"secret detail {TOKEN}"}
        elif self.output_in_operation:
            op["properties"] = {"output": {"value": self._value()}}
        return httpx2.Response(200, json=op)

    def _value(self) -> list[dict[str, str]]:
        if self._container in self.allowed:
            stdout = "status=running started_at=2026-10-13T10:00:00Z\n"
            message = script_output(0, stdout)
        else:
            message = script_output(65, f"not allow-listed: {self._container}\n")
        return [{"code": "ProvisioningState/succeeded", "level": "Info", "message": message}]

    @staticmethod
    def _error(status: int) -> httpx2.Response:
        headers = {"Retry-After": "3"} if status in (429, 503) else {}
        return httpx2.Response(
            status, json={"error": {"code": f"Code{status}", "message": "m"}}, headers=headers
        )


class FakeTime:
    def __init__(self) -> None:
        self.now, self.sleeps = 0.0, list[float]()

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_executor(
    arm: FakeArm, time: FakeTime | None = None, **settings: Any
) -> tuple[AzureRunCommandExecutor, StaticCredentials]:
    time = time or FakeTime()
    creds = StaticCredentials(TOKEN)
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(arm.handler))
    executor = AzureRunCommandExecutor(
        http,
        creds,
        AzureSettings(**({"subscription_id": SUB} | settings)),
        sleep=time.sleep,
        clock=time.clock,
        owns_http=True,
    )
    return executor, creds


def azure_target(
    container: str = "demo-orders-api", resource_id: str = VM_ID, timeout: int = 20
) -> RestartTarget:
    """`model_copy(update=...)` skips validation on purpose: it simulates a registry entry that got past the
    loader, so the executor's own checks are what the tests exercise."""
    target = RestartTarget.model_validate(
        {
            "id": "orders-api-vm01",
            "description": "azure test",
            "match": {"entity_ids": ["X"]},
            "executor": {"kind": "azure_run_command", "container": "placeholder", "resource_id": VM_ID},
            "policy": {"autonomy": "approval_required", "restart_timeout_seconds": timeout},
        }
    )
    executor = target.executor.model_copy(update={"container": container, "resource_id": resource_id})
    return target.model_copy(update={"executor": executor})


@pytest.fixture
def arm_kit() -> SimpleNamespace:
    """Test helpers for the Run Command executor (the importlib import mode cannot import sibling modules)."""
    return SimpleNamespace(
        SUB=SUB,
        VM_ID=VM_ID,
        ARM=ARM,
        TOKEN=TOKEN,
        OP_URL=OP_URL,
        RESULT_URL=RESULT_URL,
        FakeArm=FakeArm,
        FakeTime=FakeTime,
        make_executor=make_executor,
        azure_target=azure_target,
        script_output=script_output,
    )
