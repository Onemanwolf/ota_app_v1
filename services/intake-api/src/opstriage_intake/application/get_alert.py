from __future__ import annotations

from uuid import UUID

from opstriage_intake.domain.alert import AlertView
from opstriage_intake.ports.inbox import InboxRepository


class GetAlert:
    def __init__(self, inbox: InboxRepository) -> None:
        self._inbox = inbox

    async def __call__(self, alert_id: UUID) -> AlertView | None:
        return await self._inbox.get(alert_id)
