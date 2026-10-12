from __future__ import annotations

from typing import Any, Protocol

from opstriage_contracts.tools import NotificationResult
from opstriage_notification.domain.email import OutboundEmail


class ChannelDeliveryError(RuntimeError):
    def __init__(self, channel: str, detail: str) -> None:
        super().__init__(f"{channel} delivery failed: {detail}")
        self.channel, self.detail = channel, detail


class TeamsSender(Protocol):
    async def send(self, message: dict[str, Any]) -> None: ...


class EmailSender(Protocol):
    async def send(self, mail: OutboundEmail) -> None: ...


class DeliveryStore(Protocol):
    async def get(self, idempotency_key: str) -> NotificationResult | None: ...

    async def save(self, result: NotificationResult, *, kind: str, severity: str, title: str) -> bool:
        """False when the key already exists (a concurrent duplicate)."""
        ...


class RateLimiter(Protocol):
    def try_acquire(self, channel: str) -> bool: ...
