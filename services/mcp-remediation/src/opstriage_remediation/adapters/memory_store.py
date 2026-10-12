from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from opstriage_contracts.tools import RemediationStatus
from opstriage_remediation.domain.execution import Execution


@dataclass(frozen=True, slots=True)
class Rejection:
    idempotency_key: str
    target_id: str
    problem_id: str
    reason: str


class InMemoryExecutionStore:
    """Fake ExecutionStore for unit and contract tests."""

    def __init__(self) -> None:
        self.executions: dict[str, Execution] = {}
        self.rejections: list[Rejection] = []

    async def get(self, idempotency_key: str) -> Execution | None:
        return self.executions.get(idempotency_key)

    async def insert(self, execution: Execution) -> bool:
        if execution.idempotency_key in self.executions:
            return False
        self.executions[execution.idempotency_key] = execution
        return True

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
