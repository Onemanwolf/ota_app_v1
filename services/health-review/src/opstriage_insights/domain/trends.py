"""Series arithmetic for the digest (OTA-APP-002 D1: code does the maths, the model explains it).

All functions are pure and work on MetricSeries or (timestamp, value) points; intervals with no data are
skipped.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from opstriage_insights.domain.model import MetricSeries

Point = tuple[datetime, float]
Aggregate = Literal["min", "max", "mean"]
Direction = Literal["above", "below"]
DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Stats:
    n: int
    min: float
    p50: float
    p95: float
    max: float
    mean: float
    last: float


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..1), stable for small n."""
    k = max(0, min(len(sorted_values) - 1, math.ceil(q * len(sorted_values)) - 1))
    return sorted_values[k]


def stats(series: MetricSeries) -> Stats | None:
    points = list(series.points())
    if not points:
        return None
    values = sorted(v for _, v in points)
    return Stats(
        n=len(values),
        min=values[0],
        p50=_percentile(values, 0.50),
        p95=_percentile(values, 0.95),
        max=values[-1],
        mean=statistics.fmean(values),
        last=points[-1][1],
    )


def slope_per_day(points: Sequence[Point]) -> float | None:
    """Least-squares slope in value units per day; None with fewer than 3 points or no time spread."""
    if len(points) < 3:
        return None
    t0 = points[0][0]
    xs = [(t - t0) / DAY for t, _ in points]
    ys = [v for _, v in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx


def window(points: Sequence[Point], start: datetime, end: datetime | None = None) -> list[Point]:
    return [(t, v) for t, v in points if t >= start and (end is None or t < end)]


def daily(series: MetricSeries, agg: Aggregate, *, complete_only: bool = True) -> list[Point]:
    """One point per UTC day (stamped at midnight): the day's min, max or mean.

    With `complete_only`, days with data for under 90% of their intervals are dropped: a window that starts at
    06:00 would otherwise contribute a day that missed the 01:00 peak and bend the slope.
    """
    days: dict[datetime, list[float]] = defaultdict(list)
    for t, v in series.points():
        days[t.replace(hour=0, minute=0, second=0, microsecond=0)].append(v)
    needed = 0.9 * (DAY / series.interval) if complete_only else 0
    return [(d, _aggregate(vs, agg)) for d, vs in sorted(days.items()) if len(vs) >= needed]


def _aggregate(values: Sequence[float], agg: Aggregate) -> float:
    if agg == "min":
        return min(values)
    if agg == "max":
        return max(values)
    return statistics.fmean(values)


def forecast_crossing(
    points: Sequence[Point], threshold: float, now: datetime, direction: Direction
) -> timedelta | None:
    """Time until a linear fit of `points` goes `direction` the threshold (disk "above" 90, idle "below" 0.3).

    timedelta(0) when the last value is already past it; None when the fit is flat or moves away from it.
    """
    if not points:
        return None
    last = points[-1][1]
    if (direction == "above" and last >= threshold) or (direction == "below" and last <= threshold):
        return timedelta(0)
    slope = slope_per_day(points)
    if slope is None or slope == 0 or (slope > 0) != (direction == "above"):
        return None
    t0 = points[0][0]
    xs = [(t - t0) / DAY for t, _ in points]
    mx, my = statistics.fmean(xs), statistics.fmean(v for _, v in points)
    intercept = my - slope * mx
    days_at_crossing = (threshold - intercept) / slope
    return max(t0 + days_at_crossing * DAY - now, timedelta(0))


def relative_change(
    points: Sequence[Point], now: datetime, recent: timedelta, baseline: timedelta
) -> float | None:
    """mean(last `recent`) / mean(the `baseline` before it) - 1. None without data or with a zero baseline."""
    cur = [v for _, v in window(points, now - recent, now)]
    base = [v for _, v in window(points, now - recent - baseline, now - recent)]
    if not cur or not base:
        return None
    b = statistics.fmean(base)
    if b == 0:
        return None
    return statistics.fmean(cur) / b - 1


def recurring_hours(
    series: MetricSeries, threshold: float, min_fraction: float = 0.7, min_days: int = 5
) -> list[int]:
    """UTC hours of day in which the value exceeds `threshold` on at least `min_fraction` of the days
    observed.

    A spike that recurs at the same hour every day (a nightly batch) is a pattern, not an emerging problem.
    """
    days_seen: set[datetime] = set()
    breach_days: dict[int, set[datetime]] = defaultdict(set)
    for t, v in series.points():
        day = t.replace(hour=0, minute=0, second=0, microsecond=0)
        days_seen.add(day)
        if v > threshold:
            breach_days[t.hour].add(day)
    if len(days_seen) < min_days:
        return []
    return sorted(h for h, ds in breach_days.items() if len(ds) / len(days_seen) >= min_fraction)


def first_departure(
    series: MetricSeries, sigmas: float = 4.0, baseline_fraction: float = 0.25
) -> datetime | None:
    """First timestamp after the baseline period whose value leaves mean ± sigmas·stdev of that period.

    Used to order signals for root-cause analysis (which moved first). A flat baseline uses 5% of its mean
    (or 1e-9 when the mean is 0) as the spread, so any real change counts.
    """
    points = list(series.points())
    n_base = max(3, int(len(points) * baseline_fraction))
    if len(points) <= n_base:
        return None
    base = [v for _, v in points[:n_base]]
    mean = statistics.fmean(base)
    spread = statistics.pstdev(base) or max(abs(mean) * 0.05, 1e-9)
    for t, v in points[n_base:]:
        if abs(v - mean) > sigmas * spread:
            return t
    return None


def downsample(series: MetricSeries, max_points: int) -> list[float | None]:
    """At most `max_points` values for a sparkline: bucket means, None where a bucket has no data."""
    values = series.values
    if len(values) <= max_points:
        return list(values)
    out: list[float | None] = []
    size = len(values) / max_points
    for i in range(max_points):
        bucket = [v for v in values[int(i * size) : int((i + 1) * size)] if v is not None]
        out.append(round(statistics.fmean(bucket), 4) if bucket else None)
    return out
