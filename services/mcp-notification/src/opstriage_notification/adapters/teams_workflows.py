from __future__ import annotations

from typing import Any

import httpx2
from pydantic import SecretStr
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from opstriage_notification.ports import ChannelDeliveryError


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx2.TransportError) or (
        isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code in {429, 500, 502, 503, 504}
    )


class WorkflowsWebhookTeamsSender:
    def __init__(self, http: httpx2.AsyncClient, webhook_url: SecretStr) -> None:
        self._http, self._url = http, webhook_url

    async def send(self, message: dict[str, Any]) -> None:
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_retryable),
                reraise=True,
                wait=wait_exponential_jitter(multiplier=1, max=20),
                stop=stop_after_attempt(5),
            ):
                with attempt:
                    resp = await self._http.post(self._url.get_secret_value(), json=message, timeout=15)
                    resp.raise_for_status()
        except httpx2.HTTPError as exc:
            # Never include the URL in errors or logs: it carries a signature.
            raise ChannelDeliveryError("teams", type(exc).__name__) from None
