"""ProblemContextSource adapter on Grail (spec 12.4.4 and 12.6.4), on the opstriage_dynatrace client.

Three read-only queries:
- logs: recent ERROR and WARN lines for the restart target (host entity IDs + container name from the target
  registry) or, without a target, for the root-cause entity; newest first, deduplicated by message template.
- the problem record (`dt.davis.problems` by `event.id`): `get_problem` (the guard's trust-but-verify
  re-read) and the `dt.davis.event_ids` that link the problem to its Davis events.
- evidence: those `dt.davis.events`, mapped to EVENT evidence for the Kafka signatures.

Context is best effort: a Grail failure or timeout in `get_context` degrades to "no logs / no evidence" and
the triage continues with the event alone (the decision model and rules handle thin context). `get_problem`
fails closed: when Grail cannot confirm the problem it returns None, so the guard skips the restart.

DQL is built only from code constants and validated identifiers (target registry entries and Dynatrace IDs
that match `^[A-Za-z0-9_.:-]{1,128}$`), embedded as JSON string literals; never from log text or model output.

INFERRED (unverified against a real tenant; check one record in a Notebook, as spec 12.4.4 asks):
- logs: `dt.smartscape.host` and `container.name` being populated on OneAgent Docker logs;
  `dt.source_entity` being a string[] on log records (the no-target filter `iAny(dt.source_entity[] == ..)`).
- dt.davis.events: `dt.smartscape_source.id` / `.type` (falling back to the deprecated `dt.source_entity` /
  `.type`), `event.type`, `event.name`, `event.start`, `event.end` as the evidence fields. Davis events carry
  no root-cause-relevance flag or metric key that we know of, so Grail evidence is EVENT-only and
  `root_cause_relevant`/`metric_id` stay empty.
- dt.davis.problems: the field set mirrors the poller's problems query (spec 12.3.3); `event.id` equals the
  webhook's `{PID}` (spec section 7 identifier caution).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Sequence
from typing import Any

import structlog

from opstriage_contracts import dynatrace
from opstriage_contracts.problems import AlertSource, EntityRef, Evidence, ProblemEvent, ProblemStatus
from opstriage_contracts.tools import ResolveTargetResult, TargetSummary
from opstriage_dynatrace.grail import GrailClient
from opstriage_triage.domain.context import ProblemContext

log = structlog.get_logger()

_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_TEMPLATE_NOISE = re.compile(r"0x[0-9a-fA-F]+|[0-9a-fA-F]{8,}|\d+")
_WS = re.compile(r"\s+")
MAX_LINE_CHARS = 1_000
MAX_EVENTS = 50

_PROBLEM_FIELDS = (
    "event.id, display_id, event.name, event.category, event.status, event.severity, event.start, event.end, "
    "smartscape.affected_entities, root_cause.smartscape_entity, maintenance.is_under_maintenance, "
    "dt.davis.is_duplicate, dt.davis.impact_level, dt.davis.event_ids, timestamp"
)


def _lit(value: str) -> str:
    if not _SAFE.match(value):
        raise ValueError("identifier is not safe for DQL")
    return json.dumps(value)


def _set(values: Sequence[str]) -> str:
    return "{" + ", ".join(_lit(v) for v in values) + "}"


def logs_dql(
    target: TargetSummary | None, root_cause_id: str | None, window_minutes: int, limit: int
) -> str | None:
    """None when there is nothing safe to scope the query to (no unscoped log scans)."""
    if target is not None and _SAFE.match(target.container):
        hosts = [h for h in target.host_entity_ids if _SAFE.match(h)]
        scope = f"filter container.name == {_lit(target.container)}"
        if hosts:
            ids = _set(hosts)
            scope = f"filter in(dt.entity.host, {ids}) or in(dt.smartscape.host, {ids})\n| {scope}"
    elif root_cause_id and _SAFE.match(root_cause_id):
        scope = f"filter iAny(dt.source_entity[] == {_lit(root_cause_id)})"
    else:
        return None
    return (
        f"fetch logs, from: now() - {int(window_minutes)}m\n"
        f"| {scope}\n"
        '| filter in(status, {"ERROR", "WARN"})\n'
        "| sort timestamp desc\n"
        "| fields timestamp, status, content\n"
        f"| limit {int(limit)}"
    )


def problem_dql(problem_id: str, lookback_days: int) -> str:
    return (
        f"fetch dt.davis.problems, from: now() - {int(lookback_days)}d\n"
        f"| filter event.id == {_lit(problem_id)}\n"
        "| sort timestamp desc\n"
        "| limit 1\n"
        f"| fields {_PROBLEM_FIELDS}"
    )


def events_dql(event_ids: Sequence[str], lookback_days: int) -> str:
    return (
        f"fetch dt.davis.events, from: now() - {int(lookback_days)}d\n"
        f"| filter in(event.id, {_set(event_ids)})\n"
        "| fields event.id, event.name, event.type, event.start, event.end, dt.smartscape_source.id, "
        "dt.smartscape_source.type, dt.source_entity, dt.source_entity.type\n"
        f"| limit {MAX_EVENTS}"
    )


def log_line(record: dict[str, Any]) -> str:
    """`<timestamp to the second>Z <STATUS> <content>`: the fixture format the fake context uses."""
    ts = str(record.get("timestamp") or "")
    ts = f"{ts[:19]}Z" if len(ts) >= 19 and ts[10:11] == "T" else ts
    content = _WS.sub(" ", str(record.get("content") or "")).strip()
    return " ".join(p for p in (ts, str(record.get("status") or ""), content) if p)[:MAX_LINE_CHARS]


def dedupe_by_template(lines: Sequence[str], limit: int) -> tuple[str, ...]:
    """Keep the first (newest) line per message template: numbers and hex IDs do not make a line new."""
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        # the template ignores the timestamp and status prefix, so it is taken from the content
        key = _TEMPLATE_NOISE.sub("#", line.split(" ", 2)[-1])
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= limit:
            break
    return tuple(out)


def problem_event(r: dict[str, Any]) -> ProblemEvent:
    entities = (dynatrace.smartscape_entity(x) for x in (r.get("smartscape.affected_entities") or []))
    affected = tuple(e for e in entities if e)
    return ProblemEvent(
        source=AlertSource.DYNATRACE_POLLER_GRAIL,
        problem_id=str(r["event.id"]),
        display_id=r.get("display_id"),
        status=ProblemStatus(str(r["event.status"]).upper()),
        title=r.get("event.name") or "(untitled problem)",
        category=dynatrace.category(r.get("event.category")),
        severity=r.get("event.severity"),
        started_at=dynatrace.grail_time(r["event.start"]),
        ended_at=dynatrace.grail_time(r.get("event.end")),
        affected_entities=affected,
        root_cause_entity=dynatrace.smartscape_entity(r.get("root_cause.smartscape_entity")),
        tags={"maintenance": "true"} if r.get("maintenance.is_under_maintenance") else {},
        observed_at=dynatrace.grail_time(r.get("timestamp") or r["event.start"]),
        impact_levels=dynatrace.impact_levels(r.get("dt.davis.impact_level")),
        is_duplicate=bool(r.get("dt.davis.is_duplicate")),
    )


def evidence(r: dict[str, Any]) -> Evidence:
    source_id = r.get("dt.smartscape_source.id") or r.get("dt.source_entity")
    source_type = r.get("dt.smartscape_source.type") or r.get("dt.source_entity.type")
    entity = None
    if isinstance(source_id, str) and source_id:
        kind = source_type or source_id.split("-", 1)[0]
        entity = EntityRef(id=source_id, type=dynatrace.entity_type(kind))
    event_type = r.get("event.type")
    return Evidence(
        evidence_type="EVENT",
        display_name=str(r.get("event.name") or event_type or "Davis event")[:200],
        entity=entity,
        started_at=dynatrace.grail_time(r.get("event.start")),
        ended_at=dynatrace.grail_time(r.get("event.end")),
        event_type=str(event_type) if event_type else None,
        event_id=str(r["event.id"]) if r.get("event.id") else None,
    )


class GrailProblemContextSource:
    def __init__(
        self,
        client: GrailClient,
        *,
        max_log_lines: int,
        log_window_minutes: int = 30,
        problem_lookback_days: int = 30,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._client, self._max = client, max_log_lines
        self._window, self._lookback, self._timeout = (
            log_window_minutes,
            problem_lookback_days,
            timeout_seconds,
        )

    async def get_context(self, event: ProblemEvent, resolved: ResolveTargetResult) -> ProblemContext:
        root = event.root_cause_entity.id if event.root_cause_entity else None
        logs, found = await asyncio.gather(
            self._degrade("logs", self._logs(resolved.target, root), ()),
            self._degrade("evidence", self._evidence(event.problem_id), ()),
        )
        return ProblemContext(
            event=event,
            target=resolved.target,
            recent_restarts_24h=resolved.recent_restarts_24h,
            logs=logs,
            evidence=found,
        )

    async def get_problem(self, problem_id: str) -> ProblemEvent | None:
        record = await self._degrade("problem", self._problem(problem_id), None)
        return problem_event(record) if record else None

    async def _logs(self, target: TargetSummary | None, root_cause_id: str | None) -> tuple[str, ...]:
        if self._max <= 0:
            return ()
        limit = min(self._max * 4, 1_000)  # headroom for template deduplication
        dql = logs_dql(target, root_cause_id, self._window, limit)
        if dql is None:
            return ()
        rows = await self._client.query(dql, max_records=limit)
        return dedupe_by_template([log_line(r) for r in rows], self._max)

    async def _problem(self, problem_id: str) -> dict[str, Any] | None:
        if not _SAFE.match(problem_id):
            return None
        rows = await self._client.query(problem_dql(problem_id, self._lookback), max_records=1)
        return rows[0] if rows else None

    async def _evidence(self, problem_id: str) -> tuple[Evidence, ...]:
        record = await self._problem(problem_id)
        ids = [
            i for i in (record or {}).get("dt.davis.event_ids") or [] if isinstance(i, str) and _SAFE.match(i)
        ]
        if not ids:
            return ()
        rows = await self._client.query(events_dql(ids[:MAX_EVENTS], self._lookback), max_records=MAX_EVENTS)
        return tuple(evidence(r) for r in rows)

    async def _degrade[T](self, part: str, call: Awaitable[T], fallback: T) -> T:
        try:
            async with asyncio.timeout(self._timeout):
                return await call
        except Exception as exc:  # any Grail failure degrades; the run continues
            # exception type only: messages can echo query text or response bodies
            log.warning("grail_context_unavailable", part=part, error=type(exc).__name__)
            return fallback
