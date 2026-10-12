from __future__ import annotations

from typing import Literal

import httpx2
from pydantic import SecretStr
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from opstriage_contracts.problems import ProblemEvent
from opstriage_poller.ports import IntakeRejected


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx2.TransportError) or (
        isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code >= 500
    )


class HttpIntakeClient:
    """POST /v1/alerts. 202 accepted, 200 duplicate; retries transport errors and 5xx; raises on 4xx."""

    def __init__(self, http: httpx2.AsyncClient, base_url: str, token: SecretStr, attempts: int = 5) -> None:
        self._http, self._url, self._token, self._attempts = (
            http,
            f"{base_url.rstrip('/')}/v1/alerts",
            token,
            attempts,
        )

    async def submit(self, event: ProblemEvent) -> Literal["accepted", "duplicate"]:
        headers = {"Authorization": f"Bearer {self._token.get_secret_value()}"}
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_retryable),
            reraise=True,
            wait=wait_exponential_jitter(initial=0.5, max=10),
            stop=stop_after_attempt(self._attempts),
        ):
            with attempt:
                resp = await self._http.post(self._url, json=event.model_dump(mode="json"), headers=headers)
                if resp.status_code >= 500:
                    resp.raise_for_status()
        if resp.status_code == 202:
            return "accepted"
        if resp.status_code == 200:
            return "duplicate"
        raise IntakeRejected(f"intake rejected {event.problem_id}: HTTP {resp.status_code}")
