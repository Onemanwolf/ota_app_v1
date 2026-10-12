from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opstriage_remediation.adapters.dry_run import DryRunOnlyExecutor
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
def store() -> InMemoryExecutionStore:
    return InMemoryExecutionStore()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_service(
    registry: TargetRegistry, store: InMemoryExecutionStore, clock: FakeClock, **guard: object
) -> RemediationService:
    settings = {
        "enabled": True,
        "dry_run": True,
        "max_autonomy": "approval_required",
        "approval_ttl": timedelta(minutes=30),
        "allowed_approval_modes": {"auto_approve", "manual_api"},
    } | guard
    executor = DryRunOnlyExecutor(started_at=T0)
    return RemediationService(
        registry, store, dict.fromkeys(registry.executor_kinds(), executor), clock, GuardSettings(**settings)
    )  # type: ignore[arg-type]


@pytest.fixture
def service(registry: TargetRegistry, store: InMemoryExecutionStore, clock: FakeClock) -> RemediationService:
    return make_service(registry, store, clock)


@pytest.fixture
def service_factory(
    registry: TargetRegistry, store: InMemoryExecutionStore, clock: FakeClock
) -> Callable[..., RemediationService]:
    def factory(**guard: object) -> RemediationService:
        return make_service(registry, store, clock, **guard)

    return factory
