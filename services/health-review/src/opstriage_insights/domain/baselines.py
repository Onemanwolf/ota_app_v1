"""Deterministic Kafka checks (OTA-APP-002 D4): breaches of documented normal values become findings
whether or not the model mentions them.

Sources: "Kafka 3.8 docs" rows are the Apache Kafka monitoring docs' Normal value column (research report,
Appendix B). Rows marked INFERRED have no documented normal value and are operational judgement; each is
a setting to tune per estate. A breach must persist for `sustained` so planned restarts and short blips
do not fire.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from opstriage_insights.domain import kafka_metrics as km
from opstriage_insights.domain.model import MetricSeries

Level = Literal["warning", "critical"]
Check = Literal["above", "below", "cluster_sum_not"]
DOCS = "Kafka 3.8 docs (Appendix B)"
INFERRED = "INFERRED"


@dataclass(frozen=True, slots=True)
class BaselineRule:
    id: str
    metric: str
    check: Check
    threshold: float
    level: Level
    description: str
    source: str
    sustained: timedelta = timedelta(0)


@dataclass(frozen=True, slots=True)
class Breach:
    rule: BaselineRule
    entity: str  # series label, or "cluster" for cluster-wide checks
    first: datetime
    last: datetime
    worst: float
    active: bool  # the latest point still breaches


KAFKA_BASELINES: tuple[BaselineRule, ...] = (
    BaselineRule(
        "offline-partitions",
        km.OFFLINE,
        "above",
        0,
        "critical",
        "Offline partitions: data on them is unavailable",
        INFERRED,
    ),
    BaselineRule(
        "active-controller",
        km.ACTIVE_CONTROLLER,
        "cluster_sum_not",
        1,
        "critical",
        "Exactly one broker should be the active controller",
        DOCS,
        timedelta(minutes=10),
    ),
    BaselineRule(
        "unclean-elections",
        km.UNCLEAN,
        "above",
        0,
        "critical",
        "Unclean leader elections (normal value 0): possible data loss",
        DOCS,
    ),
    BaselineRule(
        "under-min-isr",
        km.UNDER_MIN_ISR,
        "above",
        0,
        "critical",
        "Partitions under min.insync.replicas (normal value 0): acks=all writes fail",
        DOCS,
        timedelta(minutes=10),
    ),
    BaselineRule(
        "under-replicated",
        km.URP,
        "above",
        0,
        "warning",
        "Under-replicated partitions (normal value 0) for longer than a restart",
        DOCS,
        timedelta(minutes=30),
    ),
    BaselineRule(
        "network-idle",
        km.NETWORK_IDLE,
        "below",
        0.3,
        "warning",
        "Network processors busy (ideally > 0.3 idle)",
        DOCS,
        timedelta(minutes=15),
    ),
    BaselineRule(
        "handler-idle",
        km.HANDLER_IDLE,
        "below",
        0.3,
        "warning",
        "Request handlers busy (ideally > 0.3 idle; extension key mapped to RequestHandlerAvgIdlePercent)",
        DOCS,
        timedelta(minutes=15),
    ),
    BaselineRule(
        "failed-produce",
        km.FAILED_PRODUCE,
        "above",
        0,
        "warning",
        "Produce requests failing",
        INFERRED,
        timedelta(minutes=15),
    ),
    BaselineRule(
        "disk-warning",
        km.DISK_USED,
        "above",
        75,
        "warning",
        "Broker disk above 75%",
        INFERRED,
        timedelta(hours=1),
    ),
    BaselineRule(
        "disk-critical",
        km.DISK_USED,
        "above",
        90,
        "critical",
        "Broker disk above 90%",
        INFERRED,
        timedelta(minutes=15),
    ),
    BaselineRule(
        "gc-suspension",
        km.GC_SUSPENSION,
        "above",
        10,
        "warning",
        "JVM GC suspends over 10% of time",
        INFERRED,
        timedelta(hours=1),
    ),
)


def _breaches(value: float, rule: BaselineRule) -> bool:
    if rule.check == "above":
        return value > rule.threshold
    if rule.check == "below":
        return value < rule.threshold
    return value != rule.threshold


@dataclass(slots=True)
class _Run:
    start: datetime
    end: datetime
    worst: float


def _runs(points: Sequence[tuple[datetime, float]], rule: BaselineRule, step: timedelta) -> list[_Run]:
    """Consecutive breaching points (no gap longer than one step) merged into runs."""
    worse = max if rule.check != "below" else min
    runs: list[_Run] = []
    prev: datetime | None = None
    for t, v in points:
        if _breaches(v, rule):
            if runs and prev is not None and runs[-1].end == prev and t - prev <= step:
                runs[-1].end, runs[-1].worst = t, worse(runs[-1].worst, v)
            else:
                runs.append(_Run(t, t, v))
        prev = t
    return runs


def _breach(
    points: Sequence[tuple[datetime, float]], rule: BaselineRule, step: timedelta, entity: str
) -> Breach | None:
    """One Breach per entity from the runs that last at least `sustained` (a run of n points spans n
    steps)."""
    kept = [r for r in _runs(points, rule, step) if r.end - r.start + step >= rule.sustained]
    if not kept:
        return None
    worse = max if rule.check != "below" else min
    worst = kept[0].worst
    for r in kept[1:]:
        worst = worse(worst, r.worst)
    active = bool(points) and kept[-1].end == points[-1][0]
    return Breach(rule, entity, kept[0].start, kept[-1].end, worst, active)


def evaluate(rules: Sequence[BaselineRule], series: Mapping[str, Sequence[MetricSeries]]) -> list[Breach]:
    """Breaches per entity (or per cluster for cluster-wide checks), critical first."""
    out: list[Breach] = []
    for rule in rules:
        group = series.get(rule.metric, ())
        if not group:
            continue
        step = group[0].interval
        if rule.check == "cluster_sum_not":
            totals: dict[datetime, float] = {}
            for s in group:
                for t, v in s.points():
                    totals[t] = totals.get(t, 0.0) + v
            b = _breach(sorted(totals.items()), rule, step, "cluster")
            if b:
                out.append(b)
            continue
        for s in group:
            b = _breach(list(s.points()), rule, s.interval, s.label)
            if b:
                out.append(b)
    return sorted(out, key=lambda b: (b.rule.level != "critical", b.rule.id, b.entity))
