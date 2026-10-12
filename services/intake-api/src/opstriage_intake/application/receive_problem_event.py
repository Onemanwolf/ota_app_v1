from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from opstriage_contracts.problems import ProblemEvent
from opstriage_intake.ports.ids import IdGenerator
from opstriage_intake.ports.inbox import InboxRepository


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    alert_id: UUID | None
    duplicate: bool


class ReceiveProblemEvent:
    """Single entry point for every source (webhooks, poller, simulator)."""

    def __init__(self, inbox: InboxRepository, ids: IdGenerator, max_raw_bytes: int) -> None:
        self._inbox = inbox
        self._ids = ids
        self._max_raw_bytes = max_raw_bytes

    async def __call__(self, event: ProblemEvent, raw: dict[str, Any] | None) -> ReceiveResult:
        alert_id = self._ids.new()
        stored = await self._inbox.add_if_new(alert_id=alert_id, event=event, raw=self._cap(raw))
        if not stored:
            return ReceiveResult(alert_id=None, duplicate=True)
        await self._inbox.notify_new()
        return ReceiveResult(alert_id=alert_id, duplicate=False)

    def _cap(self, raw: dict[str, Any] | None) -> dict[str, Any] | None:
        if raw is None:
            return None
        return raw if len(json.dumps(raw)) <= self._max_raw_bytes else {"truncated": True}
