"""Collection ports (OTA-APP-002 3.2). Adapters: Grail (5.4) and fixture fakes (5.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from opstriage_insights.domain.model import ClusterScope, LogRecord, MetricSeries, ProblemRecord


class LogSource(Protocol):
    async def fetch(self, scope: ClusterScope, start: datetime, end: datetime, limit: int) -> list[LogRecord]:
        """WARN and ERROR records for the scope in [start, end), newest first, at most `limit`."""
        ...


class MetricSource(Protocol):
    async def fetch(
        self, scope: ClusterScope, metric: str, start: datetime, end: datetime, interval: timedelta
    ) -> list[MetricSeries]:
        """One series per dimension combination (broker, topic, client) at `interval` resolution."""
        ...


@dataclass(frozen=True, slots=True)
class FoundProblem:
    problem: ProblemRecord
    cluster_hint: str | None = None  # fixture source: the cluster the problem belongs to


class ProblemSource(Protocol):
    async def fetch(self, scope: ClusterScope, start: datetime, end: datetime) -> list[ProblemRecord]:
        """Non-duplicate Davis problems touching the scope's entities that overlap [start, end)."""
        ...

    async def find(self, display_id: str) -> FoundProblem | None:
        """One problem by its display id (P-...), for root-cause analysis."""
        ...
