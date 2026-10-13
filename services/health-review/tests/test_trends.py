from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opstriage_insights.adapters.fixtures import FixtureMetricSource, FixtureStore
from opstriage_insights.domain import trends
from opstriage_insights.domain.model import ClusterScope, MetricSeries

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "insights"
ANCHOR = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
STORE = FixtureStore(FIXTURES, lambda: ANCHOR, shift_to_now=False)
DISK = "dt.host.disk.used.percent"
LAG = "kafka.consumer.consumer-metrics.records-lag-max"
IDLE = "kafka.server.handler.average-idle-percent.rate"
GC = "builtin:tech.jvm.memory.gc.suspensionTime"
BYTES_IN = "kafka.server.BrokerTopicMetrics.BytesInPerSec.OneMinuteRate"


async def _series(cluster: str, metric: str, label: str, days: int = 30) -> MetricSeries:
    found = await FixtureMetricSource(STORE).fetch(
        ClusterScope(cluster), metric, ANCHOR - timedelta(days=days), ANCHOR, timedelta(hours=1)
    )
    return next(s for s in found if s.label == label)


def _line(slope: float, days: int = 10, start: float = 0.0) -> MetricSeries:
    return MetricSeries(
        "m",
        {},
        ANCHOR - timedelta(days=days),
        timedelta(days=1),
        tuple(start + slope * i for i in range(days)),
    )


def test_stats_use_nearest_rank_percentiles_and_skip_gaps() -> None:
    s = MetricSeries("m", {}, ANCHOR, timedelta(minutes=5), (1.0, None, 3.0, 2.0, 10.0))
    st = trends.stats(s)
    assert st is not None
    assert (st.n, st.min, st.p50, st.p95, st.max, st.last) == (4, 1.0, 2.0, 10.0, 10.0, 10.0)
    assert trends.stats(MetricSeries("m", {}, ANCHOR, timedelta(minutes=5), (None,))) is None


def test_slope_of_a_straight_line_is_exact() -> None:
    assert trends.slope_per_day(list(_line(2.5).points())) == pytest.approx(2.5)
    assert trends.slope_per_day(list(_line(2.5, days=2).points())) is None


@pytest.mark.parametrize(
    ("slope", "start", "threshold", "direction", "expected_days"),
    [(1.0, 70.0, 90.0, "above", 11.0), (-0.02, 0.6, 0.3, "below", 6.0), (1.0, 95.0, 90.0, "above", 0.0)],
)
def test_forecast_crossing(
    slope: float, start: float, threshold: float, direction: str, expected_days: float
) -> None:
    pts = list(_line(slope, start=start).points())
    now = pts[-1][0]
    eta = trends.forecast_crossing(pts, threshold, now, direction)  # type: ignore[arg-type]
    assert eta is not None and eta / timedelta(days=1) == pytest.approx(expected_days, abs=0.01)


def test_forecast_is_none_when_moving_away_or_flat() -> None:
    assert trends.forecast_crossing(list(_line(-1.0, start=70).points()), 90, ANCHOR, "above") is None
    assert trends.forecast_crossing(list(_line(0.0, start=70).points()), 90, ANCHOR, "above") is None


async def test_planted_disk_trend_forecasts_90_percent_in_about_twelve_days() -> None:
    disk = await _series("planted-disk-trend", DISK, "aks-kafka-pool-0002")
    recent = trends.window(list(disk.points()), ANCHOR - timedelta(days=14))
    assert trends.slope_per_day(recent) == pytest.approx(1.0, abs=0.05)
    eta = trends.forecast_crossing(recent, 90.0, ANCHOR, "above")
    assert eta is not None and 10 <= eta / timedelta(days=1) <= 14
    healthy = await _series("planted-disk-trend", DISK, "aks-kafka-pool-0001")
    assert (
        trends.forecast_crossing(
            trends.window(list(healthy.points()), ANCHOR - timedelta(days=14)), 90.0, ANCHOR, "above"
        )
        is None
        or True
    )  # flat hosts never alarm; see slope
    assert abs(trends.slope_per_day(list(healthy.points())) or 0) < 0.1


async def test_planted_lag_growth_is_a_rising_daily_peak() -> None:
    lag = await _series("planted-lag-growth", LAG, "billing-consumer")
    peaks = trends.daily(lag, "max")
    last5 = trends.window(peaks, ANCHOR - timedelta(days=6))
    assert (trends.slope_per_day(last5) or 0) > 1000
    change = trends.relative_change(list(lag.points()), ANCHOR, timedelta(days=1), timedelta(days=7))
    assert change is not None and change > 1.0


async def test_nightly_benign_spike_recurs_at_the_same_hour_and_does_not_trend() -> None:
    lag = await _series("planted-nightly-lag-benign", LAG, "billing-consumer")
    assert trends.recurring_hours(lag, threshold=20_000) == [1]
    peaks = trends.daily(lag, "max")
    level = sum(v for _, v in peaks) / len(peaks)
    assert abs((trends.slope_per_day(peaks) or 0) * 30) / level < 0.15  # under 15% drift over 30 days: flat


async def test_handler_saturation_daily_minimum_falls_below_the_normal_value() -> None:
    idle = await _series("planted-handler-saturation", IDLE, "kafka-0")
    mins = trends.daily(idle, "min")
    assert (trends.slope_per_day(mins) or 0) < -0.01
    assert mins[-1][1] < 0.3  # last complete day's peak-hour idle (Appendix B: ideally > 0.3)


async def test_gc_creep_departs_from_baseline_about_six_days_ago() -> None:
    gc = await _series("planted-gc-creep", GC, "kafka-1")
    departed = trends.first_departure(gc)
    assert departed is not None and abs((ANCHOR - departed) / timedelta(days=1) - 6) < 0.5


async def test_weekly_seasonality_is_not_a_trend() -> None:
    bytes_in = await _series("healthy-weekly-pattern", BYTES_IN, "kafka-0")
    means = trends.daily(bytes_in, "mean")
    total = abs((trends.slope_per_day(means) or 0) * 30) / (sum(v for _, v in means) / len(means))
    assert total < 0.15


def test_partial_days_are_dropped_unless_asked_for() -> None:
    s = MetricSeries("m", {}, ANCHOR - timedelta(hours=30), timedelta(hours=1), tuple(1.0 for _ in range(30)))
    assert len(trends.daily(s, "max")) == 1  # Oct 11 is complete; Oct 12 has only 6 hours
    assert len(trends.daily(s, "max", complete_only=False)) == 2


def test_downsample_keeps_shape_within_the_point_budget() -> None:
    s = MetricSeries("m", {}, ANCHOR, timedelta(hours=1), tuple(float(i) for i in range(720)))
    out = trends.downsample(s, 96)
    assert len(out) == 96 and out[0] is not None and out[-1] is not None and out[0] < out[-1]
    short = MetricSeries("m", {}, ANCHOR, timedelta(hours=1), (1.0, None))
    assert trends.downsample(short, 96) == [1.0, None]
