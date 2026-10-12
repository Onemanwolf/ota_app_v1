"""Classic Problems API v2 adapter (spec 12.3.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from opstriage_contracts.problems import AlertSource, EntityRef, ProblemEvent, ProblemStatus
from opstriage_poller.settings import ClassicSettings

_STATUS = {"OPEN": ProblemStatus.ACTIVE, "CLOSED": ProblemStatus.CLOSED}


def _ms(value: int | None) -> datetime | None:
    return None if value is None or value < 0 else datetime.fromtimestamp(value / 1000, tz=UTC)


def _entity(e: dict[str, Any] | None) -> EntityRef | None:
    if not e:
        return None
    eid = e["entityId"]
    return EntityRef(id=eid["id"], type=str(eid.get("type", "unknown")).lower(), name=e.get("name"))


def map_problem(p: dict[str, Any], observed_at: datetime) -> ProblemEvent:
    started = _ms(p.get("startTime")) or observed_at
    ended = _ms(p.get("endTime"))
    return ProblemEvent(
        source=AlertSource.DYNATRACE_POLLER_CLASSIC,
        problem_id=p["problemId"],
        display_id=p.get("displayId"),
        status=_STATUS[p["status"]],
        title=p.get("title") or "(untitled problem)",
        category=p.get("severityLevel"),
        started_at=started,
        ended_at=ended,
        affected_entities=tuple(e for e in (_entity(x) for x in p.get("affectedEntities") or []) if e),
        root_cause_entity=_entity(p.get("rootCauseEntity")),
        observed_at=ended or observed_at,  # the API has no last-update field
    )


class ClassicProblemSource:
    def __init__(
        self, http: httpx2.AsyncClient, settings: ClassicSettings, lookback: timedelta, clock: Any
    ) -> None:
        self._http, self._s, self._lookback, self._clock = http, settings, lookback, clock
        self._url = f"{settings.environment_url.rstrip('/')}/api/v2/problems"

    async def fetch_changed(self, since: datetime, lookback: timedelta) -> list[ProblemEvent]:
        # Whether `from` selects overlapping or changed problems is undocumented: use the lookback and
        # rely on intake deduplication (spec 12.3.4).
        start = min(since, self._clock.now() - lookback)
        return await self._fetch({"from": int(start.timestamp() * 1000)})

    async def fetch_active(self) -> list[ProblemEvent]:
        start = self._clock.now() - self._lookback
        return await self._fetch({"from": int(start.timestamp() * 1000), "problemSelector": 'status("open")'})

    async def _fetch(self, first_params: dict[str, Any]) -> list[ProblemEvent]:
        headers = {"Authorization": f"Api-Token {self._s.api_token.get_secret_value()}"}
        params: dict[str, Any] = {**first_params, "pageSize": self._s.page_size}
        events: list[ProblemEvent] = []
        observed = self._clock.now()
        while True:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(httpx2.TransportError),
                wait=wait_exponential_jitter(initial=1, max=10),
                stop=stop_after_attempt(4),
                reraise=True,
            ):
                with attempt:
                    resp = await self._http.get(self._url, headers=headers, params=params)
            resp.raise_for_status()
            body = resp.json()
            events += [map_problem(p, observed) for p in body.get("problems", [])]
            next_key = body.get("nextPageKey")
            if not next_key:
                return events
            params = {"nextPageKey": next_key}  # with nextPageKey, send no other query parameters
