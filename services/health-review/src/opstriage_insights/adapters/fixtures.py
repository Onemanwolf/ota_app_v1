"""Fixture-backed fakes for LogSource, MetricSource and ProblemSource (INSIGHTS__SOURCE=fake).

Layout (built by fixtures/insights/generate.py), one directory per cluster:
    <fixtures_dir>/<cluster>/scenario.json   anchor time, planted issue, expected findings
    <fixtures_dir>/<cluster>/logs.json       raw `fetch logs` records
    <fixtures_dir>/<cluster>/metrics.json    {metric key: {"5m" | "1h": [raw `timeseries` records]}}
    <fixtures_dir>/<cluster>/problems.json   raw `dt.davis.problems` records

With `shift_to_now`, every timestamp moves by (now - anchor), so a fixture recorded for 2026-10-12 06:00 looks
current in a live demo. Tests pass shift_to_now=False and a fixed clock.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from opstriage_insights.adapters.grail_records import log_record, metric_series, problem_record
from opstriage_insights.domain.model import ClusterScope, LogRecord, MetricSeries, ProblemRecord

RESOLUTIONS = {timedelta(minutes=5): "5m", timedelta(hours=1): "1h"}


class FixtureNotFound(LookupError):
    pass


class FixtureStore:
    def __init__(
        self,
        root: Path,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        *,
        shift_to_now: bool = True,
    ) -> None:
        self._root, self._now, self._shift_to_now = root, now, shift_to_now

    def clusters(self) -> list[str]:
        return sorted(p.parent.name for p in self._root.glob("*/scenario.json"))

    def scenario(self, cluster: str) -> dict[str, Any]:
        return dict(_read(self._dir(cluster) / "scenario.json"))

    def shift(self, cluster: str) -> timedelta:
        if not self._shift_to_now:
            return timedelta(0)
        anchor = datetime.fromisoformat(self.scenario(cluster)["anchor"])
        return self._now() - anchor

    def raw(self, cluster: str, name: str) -> Any:
        return _read(self._dir(cluster) / f"{name}.json")

    def _dir(self, cluster: str) -> Path:
        d = self._root / cluster
        if not cluster or "/" in cluster or ".." in cluster or not (d / "scenario.json").is_file():
            raise FixtureNotFound(cluster)
        return d


@cache
def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class FixtureLogSource:
    def __init__(self, store: FixtureStore) -> None:
        self._store = store

    async def fetch(self, scope: ClusterScope, start: datetime, end: datetime, limit: int) -> list[LogRecord]:
        shift = self._store.shift(scope.name)
        out = []
        for raw in self._store.raw(scope.name, "logs"):
            rec = log_record(raw)
            rec = replace(rec, timestamp=rec.timestamp + shift)
            if start <= rec.timestamp < end and rec.status in {"ERROR", "WARN"}:
                out.append(rec)
        out.sort(key=lambda r: r.timestamp, reverse=True)
        return out[:limit]


class FixtureMetricSource:
    def __init__(self, store: FixtureStore) -> None:
        self._store = store

    async def fetch(
        self, scope: ClusterScope, metric: str, start: datetime, end: datetime, interval: timedelta
    ) -> list[MetricSeries]:
        resolution = RESOLUTIONS.get(interval)
        if resolution is None:
            raise ValueError(f"fixtures hold 5m and 1h series, not {interval}")
        raw = self._store.raw(scope.name, "metrics").get(metric, {}).get(resolution, [])
        shift = self._store.shift(scope.name)
        out = []
        for r in raw:
            s = metric_series(r, metric)
            s = replace(s, start=s.start + shift)
            first = max(0, _ceil_div(start - s.start, s.interval))
            last = min(len(s.values), _ceil_div(end - s.start, s.interval))
            if first < last:
                out.append(replace(s, start=s.start + first * s.interval, values=s.values[first:last]))
        return out


class FixtureProblemSource:
    def __init__(self, store: FixtureStore) -> None:
        self._store = store

    async def fetch(self, scope: ClusterScope, start: datetime, end: datetime) -> list[ProblemRecord]:
        shift = self._store.shift(scope.name)
        out = []
        for raw in self._store.raw(scope.name, "problems"):
            p = problem_record(raw)
            p = replace(p, start=p.start + shift, end=p.end + shift if p.end else None)
            if not p.is_duplicate and p.start < end and (p.end is None or p.end >= start):
                out.append(p)
        return out


def _ceil_div(delta: timedelta, step: timedelta) -> int:
    return -(-delta // step)
