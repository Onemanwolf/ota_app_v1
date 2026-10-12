"""Classic Problems API v2 adapter (spec 12.3.4)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx2
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from opstriage_contracts import dynatrace
from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_poller.settings import ClassicSettings

_STATUS = {"OPEN": ProblemStatus.ACTIVE, "CLOSED": ProblemStatus.CLOSED}
FIELDS = "evidenceDetails,impactAnalysis"  # must be sent on EVERY page, including with nextPageKey


def map_problem(p: dict[str, Any], observed_at: datetime) -> ProblemEvent:
    """Problems API v2 Problem -> canonical event. v2 has no numeric severity: severityLevel is a category."""
    started = dynatrace.epoch_ms(p.get("startTime")) or observed_at
    ended = dynatrace.epoch_ms(p.get("endTime"))
    affected = tuple(e for e in (dynatrace.v2_entity(x) for x in p.get("affectedEntities") or []) if e)
    return ProblemEvent(
        source=AlertSource.DYNATRACE_POLLER_CLASSIC,
        problem_id=p["problemId"],
        display_id=p.get("displayId"),
        status=_STATUS[str(p["status"]).upper()],
        title=p.get("title") or "(untitled problem)",
        category=dynatrace.category(p.get("severityLevel")),
        started_at=started,
        ended_at=ended,
        affected_entities=affected,
        root_cause_entity=dynatrace.v2_entity(p.get("rootCauseEntity")),
        tags=dynatrace.v2_tags(p.get("entityTags")),
        observed_at=ended or observed_at,  # the API has no last-update field
        impact_levels=dynatrace.impact_levels(p.get("impactLevel")),
        evidence=dynatrace.v2_evidence(p.get("evidenceDetails")),
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
        params: dict[str, Any] = {**first_params, "pageSize": self._s.page_size, "fields": FIELDS}
        events: list[ProblemEvent] = []
        observed = self._clock.now()
        while True:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(httpx2.TransportError),
                wait=wait_exponential_jitter(multiplier=1, max=10),
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
            params = {"nextPageKey": next_key, "fields": FIELDS}  # nextPageKey allows only `fields`
