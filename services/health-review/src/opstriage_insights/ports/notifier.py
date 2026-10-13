"""Sends the report summary card (OTA-APP-002 3.3 step 8). Adapter: mcp-notification (Teams/e-mail)."""

from __future__ import annotations

from typing import Protocol

from opstriage_contracts.tools import NotificationRequest


class NotifyFailed(RuntimeError):
    pass


class Notifier(Protocol):
    async def send(self, request: NotificationRequest) -> None: ...
