from __future__ import annotations

from datetime import datetime

from opstriage_remediation.domain.targets import RestartTarget
from opstriage_remediation.ports.executor import ContainerStatus, ExecutionOutcome, ExecutorUnavailable


class DryRunOnlyExecutor:
    """Module 1 executor: never touches a container; reports a fake healthy status."""

    kind = "dry_run_only"

    def __init__(self, started_at: datetime) -> None:
        self._started_at = started_at

    async def restart(self, target: RestartTarget, timeout_s: int) -> ExecutionOutcome:
        raise ExecutorUnavailable("dry-run-only executor cannot restart containers")

    async def status(self, target: RestartTarget) -> ContainerStatus:
        return ContainerStatus(
            running=True, started_at=self._started_at, detail="fake status (dry-run executor)"
        )
