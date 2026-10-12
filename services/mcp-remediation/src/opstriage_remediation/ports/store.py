from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Protocol

from opstriage_contracts.tools import RemediationStatus
from opstriage_remediation.domain.execution import Execution


class ExecutionStore(Protocol):
    async def get(self, idempotency_key: str) -> Execution | None: ...

    async def insert(self, execution: Execution) -> bool:
        """False when the idempotency key already exists."""
        ...

    async def record_rejection(
        self, *, idempotency_key: str, target_id: str, problem_id: str, reason: str
    ) -> None: ...

    async def count_since(
        self, target_id: str, since: datetime, statuses: Collection[RemediationStatus]
    ) -> int: ...
