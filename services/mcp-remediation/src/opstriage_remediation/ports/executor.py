from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from opstriage_remediation.domain.targets import RestartTarget


class ExecutorUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    succeeded: bool
    output_tail: str | None = None


@dataclass(frozen=True, slots=True)
class ContainerStatus:
    running: bool
    started_at: datetime | None = None
    detail: str | None = None


class RestartExecutor(Protocol):
    kind: str

    async def restart(self, target: RestartTarget, timeout_s: int) -> ExecutionOutcome: ...

    async def status(self, target: RestartTarget) -> ContainerStatus: ...
