from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from opstriage_contracts.tools import RemediationResult, RemediationStatus


@dataclass(frozen=True, slots=True)
class Execution:
    idempotency_key: str
    target_id: str
    problem_id: str
    status: RemediationStatus
    reason: str | None
    executor: str | None
    approved_by: str | None
    started_at: datetime
    finished_at: datetime | None
    output_tail: str | None

    def to_result(self, *, duplicate: bool = False) -> RemediationResult:
        return RemediationResult(
            target_id=self.target_id,
            idempotency_key=self.idempotency_key,
            status=self.status,
            reason=self.reason,
            executor=self.executor,
            started_at=self.started_at,
            finished_at=self.finished_at,
            output_tail=self.output_tail,
            duplicate=duplicate,
        )
