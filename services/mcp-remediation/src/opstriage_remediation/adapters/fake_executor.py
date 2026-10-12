from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from opstriage_remediation.domain.targets import RestartTarget
from opstriage_remediation.ports.executor import ContainerStatus, ExecutionOutcome


class FakeExecutor:
    """In-memory containers: restart bumps the start time. Unknown containers fail cleanly."""

    kind = "fake"

    def __init__(
        self, containers: set[str] | None = None, *, fail: bool = False, delay_s: float = 0.0
    ) -> None:
        self._started: dict[str, datetime] = {
            c: datetime(2026, 1, 1, tzinfo=UTC)
            for c in (containers if containers is not None else {"demo-orders-api"})
        }
        self.fail, self.delay_s = fail, delay_s
        self.restarts: list[str] = []

    async def restart(self, target: RestartTarget, timeout_s: int) -> ExecutionOutcome:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        name = target.executor.container
        if name not in self._started:
            return ExecutionOutcome(succeeded=False, output_tail=f"no such container: {name}")
        if self.fail:
            return ExecutionOutcome(succeeded=False, output_tail="exit_code=70")
        self.restarts.append(name)
        self._started[name] = datetime.now(UTC)
        return ExecutionOutcome(succeeded=True, output_tail=f"status=running exit_code=0 ({name})")

    async def status(self, target: RestartTarget) -> ContainerStatus:
        started = self._started.get(target.executor.container)
        return ContainerStatus(
            running=started is not None, started_at=started, detail=None if started else "no such container"
        )
