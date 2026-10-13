"""Grail Query API client (spec 12.3.3): OAuth client credentials, query:execute and query:poll.

Shared by the poller (problems) and health-review (logs, metrics, problems). Callers own their DQL.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx2
from pydantic import BaseModel, Field, SecretStr
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

TERMINAL_FAILURES = {"FAILED", "CANCELLED", "RESULT_GONE"}


class GrailSettings(BaseModel):
    environment_url: str = "https://example.apps.dynatrace.com"
    oauth_client_id: str = ""
    oauth_client_secret: SecretStr = SecretStr("")
    oauth_scopes: str = (
        "storage:buckets:read storage:events:read storage:entities:read storage:smartscape:read"
    )
    sso_url: str = "https://sso.dynatrace.com/sso/oauth2/token"
    query_timeout_ms: int = Field(default=10_000, ge=1_000)

    @property
    def configured(self) -> bool:
        return bool(self.oauth_client_id and self.oauth_client_secret.get_secret_value())


class GrailQueryError(RuntimeError):
    pass


class DynatraceOAuth:
    """Client-credentials token cache. Refreshes 60 seconds before expiry."""

    def __init__(
        self, http: httpx2.AsyncClient, settings: GrailSettings, monotonic: Any = time.monotonic
    ) -> None:
        self._http, self._s, self._now = http, settings, monotonic
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def token(self) -> str:
        async with self._lock:
            if self._token and self._now() < self._expires_at - 60:
                return self._token
            resp = await self._http.post(
                self._s.sso_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._s.oauth_client_id,
                    "client_secret": self._s.oauth_client_secret.get_secret_value(),
                    "scope": self._s.oauth_scopes,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            self._token = str(body["access_token"])
            self._expires_at = self._now() + float(body.get("expires_in", 300))
            return self._token


class GrailClient:
    def __init__(
        self,
        http: httpx2.AsyncClient,
        oauth: DynatraceOAuth,
        settings: GrailSettings,
        poll_interval_s: float = 0.5,
    ) -> None:
        self._http, self._oauth = http, oauth
        self._base = f"{settings.environment_url.rstrip('/')}/platform/storage/query/v1"
        self._timeout_ms = settings.query_timeout_ms
        self._poll_interval = poll_interval_s

    async def query(self, dql: str, *, max_records: int = 5_000) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {await self._oauth.token()}"}
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(httpx2.TransportError),
            wait=wait_exponential_jitter(multiplier=1, max=10),
            stop=stop_after_attempt(4),
            reraise=True,
        ):
            with attempt:
                resp = await self._http.post(
                    f"{self._base}/query:execute",
                    headers=headers,
                    json={
                        "query": dql,
                        "requestTimeoutMilliseconds": self._timeout_ms,
                        "maxResultRecords": max_records,
                    },
                )
        resp.raise_for_status()
        body = resp.json()
        request_token = body.get("requestToken")  # poll responses do not repeat it
        while body["state"] in {"NOT_STARTED", "RUNNING"}:
            await asyncio.sleep(self._poll_interval)
            resp = await self._http.get(
                f"{self._base}/query:poll",
                headers=headers,
                params={"request-token": request_token, "request-timeout-milliseconds": 5_000},
            )
            resp.raise_for_status()
            body = resp.json()
        if body["state"] in TERMINAL_FAILURES or body["state"] != "SUCCEEDED":
            raise GrailQueryError(f"query ended in state {body['state']}")
        return list(body["result"]["records"])
