"""Notifier adapters: mcp-notification's send_notification tool, and an in-memory fake."""

from __future__ import annotations

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from opstriage_contracts.tools import NotificationRequest, NotificationResult
from opstriage_insights.ports.notifier import NotifyFailed


class McpNotifier:
    """One short-lived MCP session per call (the server is stateless); bearer token as elsewhere."""

    def __init__(self, url: str, token: SecretStr, timeout_seconds: float) -> None:
        self._url, self._token, self._timeout = url, token, httpx2.Timeout(timeout_seconds)

    async def send(self, request: NotificationRequest) -> None:
        http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
            timeout=self._timeout,
            follow_redirects=True,
        )
        try:
            async with http, Client(streamable_http_client(self._url, http_client=http)) as client:
                result = await client.call_tool(
                    "send_notification", {"request": request.model_dump(mode="json")}
                )
        except Exception as exc:  # transport, protocol: the review still completes
            raise NotifyFailed(f"send_notification failed: {type(exc).__name__}") from exc
        if result.is_error:
            raise NotifyFailed("send_notification returned an error")
        NotificationResult.model_validate(result.structured_content)


class MemoryNotifier:
    def __init__(self) -> None:
        self.sent: list[NotificationRequest] = []

    async def send(self, request: NotificationRequest) -> None:
        self.sent.append(request)
