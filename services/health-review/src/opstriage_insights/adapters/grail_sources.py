"""Grail adapters for LogSource, MetricSource and ProblemSource (OTA-APP-002 section 5).

DQL is built only from configuration (cluster scope) and code constants, never from model output or log text.
String values are embedded as JSON string literals (DQL uses double-quoted strings with backslash escapes).
Metric keys, the `process.technology` value and dimension names are INFERRED for Grail and must be checked in
a Notebook on the real tenant (OTA-APP-002 open question 1).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from opstriage_dynatrace.grail import GrailClient
from opstriage_insights.adapters.grail_records import log_record, metric_series, problem_record
from opstriage_insights.domain.model import ClusterScope, LogRecord, MetricSeries, ProblemRecord
from opstriage_insights.ports.sources import FoundProblem

ENTITY_DIMENSIONS = ("dt.entity.process_group_instance", "dt.entity.host")


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strings(values: Sequence[str]) -> str:
    return "{" + ", ".join(json.dumps(v) for v in values) + "}"


def _interval(step: timedelta) -> str:
    minutes = int(step.total_seconds() // 60)
    return f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m"


def logs_dql(scope: ClusterScope, start: datetime, end: datetime, limit: int) -> str:
    where = []
    if scope.namespace:
        where.append(f"k8s.namespace.name == {json.dumps(scope.namespace)}")
    if scope.containers:
        where.append(f"in(container.name, {_strings(scope.containers)})")
    if scope.entity_ids:
        where.append(f"in(dt.entity.process_group_instance, {_strings(scope.entity_ids)})")
    scope_filter = " and ".join(where) or "true"
    return (
        f'fetch logs, from: "{_iso(start)}", to: "{_iso(end)}"\n'
        f'| filter in(status, {{"ERROR", "WARN"}})\n'
        f"| filter {scope_filter}\n"
        "| fields timestamp, status, loglevel, content, host.name, k8s.pod.name, container.name, "
        "dt.entity.process_group_instance\n"
        "| sort timestamp desc\n"
        f"| limit {int(limit)}"
    )


def metric_dql(scope: ClusterScope, metric: str, start: datetime, end: datetime, step: timedelta) -> str:
    if not metric.replace(".", "").replace("-", "").replace("_", "").replace(":", "").isalnum():
        raise ValueError(f"unexpected metric key: {metric!r}")
    by = "`client-id`, topic" if metric.startswith("kafka.consumer.") else ", ".join(ENTITY_DIMENSIONS[:1])
    if metric.startswith("dt.host."):
        by = "dt.entity.host"
    dql = (
        f"timeseries value = avg({metric}), by: {{{by}}}, interval: {_interval(step)}, "
        f'from: "{_iso(start)}", to: "{_iso(end)}"'
    )
    if scope.entity_ids and not metric.startswith(("kafka.consumer.", "dt.host.")):
        dql += f"\n| filter in(dt.entity.process_group_instance, {_strings(scope.entity_ids)})"
    return (
        dql + "\n| fieldsAdd entity.name = coalesce(entityName(dt.entity.process_group_instance), "
        "entityName(dt.entity.host), `client-id`)"
    )


def problems_dql(scope: ClusterScope, start: datetime, end: datetime) -> str:
    ids = _strings(scope.entity_ids) if scope.entity_ids else "{}"
    return (
        f'fetch dt.davis.problems, from: "{_iso(start)}", to: "{_iso(end)}"\n'
        "| filter not(dt.davis.is_duplicate)\n"
        f"| filter iAny(in(affected_entity_ids[], {ids}))\n"
        "| fields event.id, display_id, event.name, event.category, event.status, event.start, event.end, "
        "affected_entity_ids, dt.davis.is_duplicate\n"
        "| limit 200"
    )


def problem_by_id_dql(display_id: str) -> str:
    if not display_id.startswith("P-") or not display_id[2:].isdigit():
        raise ValueError("display ids look like P-12345678")
    return (
        "fetch dt.davis.problems, from: now() - 30d\n"
        f"| filter display_id == {json.dumps(display_id)} and not(dt.davis.is_duplicate)\n"
        "| sort timestamp desc | limit 1\n"
        "| fields event.id, display_id, event.name, event.category, event.status, event.start, event.end, "
        "affected_entity_ids, dt.davis.is_duplicate"
    )


class GrailLogSource:
    def __init__(self, client: GrailClient) -> None:
        self._client = client

    async def fetch(self, scope: ClusterScope, start: datetime, end: datetime, limit: int) -> list[LogRecord]:
        rows = await self._client.query(logs_dql(scope, start, end, limit), max_records=limit)
        return [log_record(r) for r in rows]


class GrailMetricSource:
    def __init__(self, client: GrailClient) -> None:
        self._client = client

    async def fetch(
        self, scope: ClusterScope, metric: str, start: datetime, end: datetime, interval: timedelta
    ) -> list[MetricSeries]:
        rows = await self._client.query(metric_dql(scope, metric, start, end, interval), max_records=1_000)
        return [metric_series(r, metric) for r in rows]


class GrailProblemSource:
    def __init__(self, client: GrailClient) -> None:
        self._client = client

    async def fetch(self, scope: ClusterScope, start: datetime, end: datetime) -> list[ProblemRecord]:
        if not scope.entity_ids:
            return []
        return [problem_record(r) for r in await self._client.query(problems_dql(scope, start, end))]

    async def find(self, display_id: str) -> FoundProblem | None:
        rows = await self._client.query(problem_by_id_dql(display_id), max_records=1)
        return FoundProblem(problem_record(rows[0])) if rows else None
