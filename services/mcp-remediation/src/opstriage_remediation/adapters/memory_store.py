from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from opstriage_contracts.tools import RemediationStatus
from opstriage_remediation.domain.execution import Execution
from opstriage_remediation.ports.store import StartOutcome


@dataclass(frozen=True, slots=True)
class Rejection:
    idempotency_key: str
    target_id: str
    problem_id: str
    reason: str


class InMemoryExecutionStore:
    """Fake ExecutionStore with the same constraints as the SQL adapter (unit and contract tests)."""

    def __init__(self, clock: Any = None) -> None:
        self._clock = clock
        self.executions: dict[str, Execution] = {}
        self.rejections: list[Rejection] = []
        self.resets: list[tuple[str, datetime | None]] = []
        self.reset_at: datetime | None = None

    async def get(self, idempotency_key: str) -> Execution | None:
        return self.executions.get(idempotency_key)

    async def insert(self, execution: Execution) -> bool:
        if execution.idempotency_key in self.executions:
            return False
        self.executions[execution.idempotency_key] = execution
        return True

    async def start(self, execution: Execution) -> StartOutcome:
        if execution.idempotency_key in self.executions:
            return "duplicate"
        if any(
            e.target_id == execution.target_id and e.status is RemediationStatus.IN_PROGRESS
            for e in self.executions.values()
        ):
            return "busy"
        self.executions[execution.idempotency_key] = execution
        return "started"

    async def finish(
        self,
        idempotency_key: str,
        *,
        status: RemediationStatus,
        finished_at: datetime,
        reason: str | None,
        output_tail: str | None,
    ) -> None:
        e = self.executions[idempotency_key]
        self.executions[idempotency_key] = replace(
            e, status=status, finished_at=finished_at, reason=reason, output_tail=output_tail
        )

    async def record_rejection(
        self, *, idempotency_key: str, target_id: str, problem_id: str, reason: str
    ) -> None:
        self.rejections.append(Rejection(idempotency_key, target_id, problem_id, reason))

    async def count_since(
        self, target_id: str, since: datetime, statuses: Collection[RemediationStatus]
    ) -> int:
        return sum(
            1
            for e in self.executions.values()
            if e.target_id == target_id and e.started_at >= since and e.status in statuses
        )

    async def failures_since(self, since: datetime) -> int:
        return sum(
            1
            for e in self.executions.values()
            if e.status is RemediationStatus.FAILED and e.started_at >= since
        )

    async def last_breaker_reset(self) -> datetime | None:
        return self.reset_at

    async def reset_breaker(self, *, operator: str, reason: str | None) -> None:
        self.reset_at = self._clock.now() if self._clock else datetime.now(UTC)
        self.resets.append((operator, self.reset_at))

    async def fail_stale(self, target_id: str, started_before: datetime, reason: str) -> list[str]:
        keys = [
            k
            for k, e in self.executions.items()
            if e.target_id == target_id
            and e.status is RemediationStatus.IN_PROGRESS
            and e.started_at < started_before
        ]
        for k in keys:
            self.executions[k] = replace(self.executions[k], status=RemediationStatus.FAILED, reason=reason)
        return keys
