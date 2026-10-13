"""Collected-data value objects (OTA-APP-002 3.3). Source-neutral: adapters map Grail records into these."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ClusterScope:
    """What one review looks at. Queries filter on these first, because Grail bills by data scanned."""

    name: str
    namespace: str = ""
    containers: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()  # broker / consumer process group instances


@dataclass(frozen=True, slots=True)
class LogRecord:
    timestamp: datetime
    status: str  # ERROR | WARN | INFO | NONE (Grail status rollup)
    loglevel: str
    content: str  # untrusted text; redacted before storage and prompts (OTA-APP-002 section 8)
    entity_id: str | None = None
    pod: str | None = None
    host: str | None = None


@dataclass(frozen=True, slots=True)
class MetricSeries:
    metric: str
    dimensions: dict[str, str]  # e.g. {"dt.entity.process_group_instance": "...", "entity.name": "kafka-1"}
    start: datetime
    interval: timedelta
    values: tuple[float | None, ...]  # None = no data in that interval

    def points(self) -> Iterator[tuple[datetime, float]]:
        """(timestamp, value) for intervals that have data."""
        for i, v in enumerate(self.values):
            if v is not None:
                yield self.start + i * self.interval, v

    @property
    def end(self) -> datetime:
        return self.start + len(self.values) * self.interval

    @property
    def label(self) -> str:
        return self.dimensions.get("entity.name") or next(iter(self.dimensions.values()), self.metric)


@dataclass(frozen=True, slots=True)
class ProblemRecord:
    problem_id: str
    display_id: str
    title: str
    category: str | None
    status: str  # ACTIVE | CLOSED
    start: datetime
    end: datetime | None
    entity_ids: tuple[str, ...] = field(default_factory=tuple)
    is_duplicate: bool = False

    @property
    def duration(self) -> timedelta | None:
        return self.end - self.start if self.end else None
