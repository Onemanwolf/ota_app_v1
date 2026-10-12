"""Grail Query API adapter (spec 12.3.3): OAuth client credentials, query:execute and query:poll."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from string import Template
from typing import Any

import httpx2
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from opstriage_contracts import dynatrace
from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_poller.settings import GrailSettings

TERMINAL_FAILURES = {"FAILED", "CANCELLED", "RESULT_GONE"}


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


def map_record(r: dict[str, Any], source: AlertSource = AlertSource.DYNATRACE_POLLER_GRAIL) -> ProblemEvent:
    """Map one record of the problems_changed / problems_active summarize to the canonical event.

    Grail severity is kept as-is (1 = Critical ... 5 = Informational, null when unset). Root cause is usually
    absent on real problems, so it stays optional. Evidence is not on the problem record.
    """
    affected = tuple(e for e in (dynatrace.smartscape_entity(x) for x in (r.get("affected") or [])) if e)
    return ProblemEvent(
        source=source,
        problem_id=r["event.id"],
        display_id=r.get("display_id"),
        status=ProblemStatus(str(r["status"]).upper()),
        title=r.get("name") or "(untitled problem)",
        category=dynatrace.category(r.get("category")),
        severity=r.get("severity"),
        started_at=dynatrace.grail_time(r["start"]),
        ended_at=dynatrace.grail_time(r.get("end")),
        affected_entities=affected,
        root_cause_entity=dynatrace.smartscape_entity(r.get("root_cause")),
        tags={"maintenance": "true"} if r.get("maintenance") else {},
        observed_at=dynatrace.grail_time(r["last_update"]),
        impact_levels=dynatrace.impact_levels(r.get("impact_level")),
        is_duplicate=bool(r.get("dup")),
    )


def _query(name: str) -> Template:
    return Template(files("opstriage_poller.adapters.queries").joinpath(name).read_text(encoding="utf-8"))


def render_changed(since: datetime, lookback: timedelta) -> str:
    """Only these adapter-computed values are substituted; never user-provided strings."""
    return _query("problems_changed.dql").substitute(
        lookback_hours=int(lookback.total_seconds() // 3600),
        since_iso=since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    )


def render_active(lookback: timedelta) -> str:
    return _query("problems_active.dql").substitute(lookback_hours=int(lookback.total_seconds() // 3600))


class GrailProblemSource:
    def __init__(self, client: GrailClient, lookback: timedelta) -> None:
        self._client, self._lookback = client, lookback

    async def fetch_changed(self, since: datetime, lookback: timedelta) -> list[ProblemEvent]:
        return [map_record(r) for r in await self._client.query(render_changed(since, lookback))]

    async def fetch_active(self) -> list[ProblemEvent]:
        return [map_record(r) for r in await self._client.query(render_active(self._lookback))]
