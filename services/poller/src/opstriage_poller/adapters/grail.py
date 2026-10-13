"""Grail problems source (spec 12.3.3). The Query API client lives in opstriage_dynatrace."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from string import Template
from typing import Any

from opstriage_contracts import dynatrace
from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_dynatrace.grail import DynatraceOAuth, GrailClient, GrailQueryError

__all__ = ["DynatraceOAuth", "GrailClient", "GrailProblemSource", "GrailQueryError", "map_record"]


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
