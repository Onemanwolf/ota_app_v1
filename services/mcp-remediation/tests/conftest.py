from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from opstriage_remediation.adapters.fake_executor import FakeExecutor
from opstriage_remediation.adapters.memory_store import InMemoryExecutionStore
from opstriage_remediation.application.service import GuardSettings, RemediationService
from opstriage_remediation.bootstrap import load_registry
from opstriage_remediation.domain.targets import TargetRegistry

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
