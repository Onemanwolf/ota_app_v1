"""Executor registry (spec 8.3, Open/closed): adding an executor kind never changes the use case."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from opstriage_remediation.domain.targets import ExecutorKind
from opstriage_remediation.ports.executor import RestartExecutor
from opstriage_remediation.settings import Settings

Builder = Callable[[Settings], RestartExecutor]
_F = TypeVar("_F", bound=Builder)


class ExecutorRegistry:
    def __init__(self) -> None:
        self._builders: dict[ExecutorKind, Builder] = {}

    def register(self, kind: ExecutorKind) -> Callable[[_F], _F]:
        def decorator(builder: _F) -> _F:
            if kind in self._builders:
                raise ValueError(f"executor already registered: {kind}")
            self._builders[kind] = builder
            return builder

        return decorator

    def build_all(self, settings: Settings, kinds: set[ExecutorKind]) -> dict[ExecutorKind, RestartExecutor]:
        """Only kinds referenced by the target registry are built: a local stack needs no Azure creds."""
        missing = kinds - self._builders.keys()
        if missing:
            raise ValueError(f"no executor registered for: {sorted(missing)}")
        return {kind: self._builders[kind](settings) for kind in kinds}


registry = ExecutorRegistry()


@registry.register(ExecutorKind.LOCAL_DOCKER)
def _local_docker(settings: Settings) -> RestartExecutor:
    if not (settings.app_env == "local" and settings.remediation.allow_local_docker):
        raise ValueError("local_docker requires APP_ENV=local and REMEDIATION__ALLOW_LOCAL_DOCKER=true")
    from opstriage_remediation.adapters.local_docker import LocalDockerExecutor

    return LocalDockerExecutor.from_env()


# azure_run_command, arc_run_command and ssh are designed in spec 12.5.2 and register here when built
# (out of the local scope: BUILD-PLAN section 2). A registry that references them fails fast at startup.
