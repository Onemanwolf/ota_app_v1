"""Map raw Grail query records to domain objects. Shared by the Grail adapters and the fixture fakes, so the
fixtures double as contract fixtures for the record shapes.

Field names: `fetch logs` and `dt.davis.problems` fields are DOCUMENTED (reports/Dynatrace Kafka triage
data models.md). The `timeseries` record shape (`timeframe`, `interval`, one array column per aggregation)
follows the DQL docs. The JSON encoding of `interval` (nanoseconds as a string) is INFERRED; strings and
integers are both read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from opstriage_insights.domain.model import LogRecord, MetricSeries, ProblemRecord

_LEVELS = {"ERROR", "WARN", "INFO", "NONE"}


def grail_time(value: str | None) -> datetime | None:
    """Grail timestamps are ISO 8601 with up to nanosecond precision and a Z suffix."""
    if not value:
        return None
    text = value.rstrip("Z")
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"  # Python keeps microseconds
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def _required_time(value: str | None, field: str) -> datetime:
    t = grail_time(value)
    if t is None:
        raise ValueError(f"record has no {field}")
    return t


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value) if value else None


def log_record(r: dict[str, Any]) -> LogRecord:
    status = str(r.get("status") or "NONE").upper()
    return LogRecord(
        timestamp=_required_time(r.get("timestamp"), "timestamp"),
        status=status if status in _LEVELS else "NONE",
        loglevel=str(r.get("loglevel") or status),
        content=str(r.get("content") or ""),
        entity_id=_first(r.get("dt.entity.process_group_instance") or r.get("dt.source_entity")),
        pod=r.get("k8s.pod.name"),
        host=r.get("host.name"),
    )


def _interval(value: Any) -> timedelta:
    if isinstance(value, dict):  # {"seconds": n} style, tolerated
        return timedelta(seconds=float(value.get("seconds", 0)))
    return timedelta(microseconds=int(value) / 1000)  # nanoseconds


def metric_series(r: dict[str, Any], metric: str, column: str = "value") -> MetricSeries:
    frame = r["timeframe"]
    values = r[column]
    dims = {k: str(v) for k, v in r.items() if k not in {"timeframe", "interval", column} and v is not None}
    return MetricSeries(
        metric=metric,
        dimensions=dims,
        start=_required_time(frame["start"], "timeframe.start"),
        interval=_interval(r["interval"]),
        values=tuple(None if v is None else float(v) for v in values),
    )


def problem_record(r: dict[str, Any]) -> ProblemRecord:
    return ProblemRecord(
        problem_id=str(r["event.id"]),
        display_id=str(r.get("display_id") or r["event.id"]),
        title=str(r.get("event.name") or "(untitled problem)"),
        category=r.get("event.category"),
        status=str(r.get("event.status") or "ACTIVE").upper(),
        start=_required_time(r.get("event.start"), "event.start"),
        end=grail_time(r.get("event.end")),
        entity_ids=tuple(str(x) for x in r.get("affected_entity_ids") or ()),
        is_duplicate=bool(r.get("dt.davis.is_duplicate")),
    )
