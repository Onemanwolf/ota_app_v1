from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from opstriage_contracts.problems import ProblemEvent
from opstriage_intake.domain.alert import AlertView


class InboxRepository(Protocol):
    async def add_if_new(self, *, alert_id: UUID, event: ProblemEvent, raw: dict[str, Any] | None) -> bool:
        """Insert unless the dedup key (or display key) exists. True when stored."""
        ...

    async def notify_new(self) -> None: ...

    async def get(self, alert_id: UUID) -> AlertView | None: ...
