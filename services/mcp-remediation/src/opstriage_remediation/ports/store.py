from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Literal, Protocol

from opstriage_contracts.tools import RemediationStatus
from opstriage_remediation.domain.execution import Execution

StartOutcome = Literal["started", "duplicate", "busy"]


class ExecutionStore(Protocol):
    async def get(self, idempotency_key: str) -> Execution | None: ...

    async def insert(self, execution: Execution) -> bool:
        """Insert a terminal row (dry run). False when the idempotency key already exists."""
        ...

    async def start(self, execution: Execution) -> StartOutcome:
        """Insert an IN_PROGRESS row. That insert is the per-target lock (partial unique index):
        'busy' when another restart of the target is in flight, 'duplicate' when the key exists."""
        ...

    async def finish(
        self,
        idempotency_key: str,
        *,
        status: RemediationStatus,
        finished_at: datetime,
        reason: str | None,
        output_tail: str | None,
    ) -> None: ...

    async def record_rejection(
        self, *, idempotency_key: str, target_id: str, problem_id: str, reason: str
    ) -> None: ...

    async def count_since(
        self, target_id: str, since: datetime, statuses: Collection[RemediationStatus]
    ) -> int: ...

    async def failures_since(self, since: datetime) -> int:
        """FAILED executions of any target since `since` (circuit breaker)."""
        ...

    async def last_breaker_reset(self) -> datetime | None: ...

    async def reset_breaker(self, *, operator: str, reason: str | None) -> None: ...

    async def fail_stale(self, target_id: str, started_before: datetime, reason: str) -> list[str]:
        """Mark IN_PROGRESS rows started before the cutoff as FAILED. Returns their keys."""
        ...
