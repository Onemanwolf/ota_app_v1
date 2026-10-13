from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from opstriage_insights.adapters.fixtures import FixtureMetricSource, FixtureStore
from opstriage_insights.domain import kafka_metrics as km
from opstriage_insights.domain.baselines import KAFKA_BASELINES, BaselineRule, Breach, evaluate
from opstriage_insights.domain.model import ClusterScope, MetricSeries

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "insights"
ANCHOR = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
STORE = FixtureStore(FIXTURES, lambda: ANCHOR, shift_to_now=False)
FIVE = timedelta(minutes=5)


async def _breaches(cluster: str) -> list[Breach]:
    source = FixtureMetricSource(STORE)
    series = {
        m: await source.fetch(ClusterScope(cluster), m, ANCHOR - timedelta(hours=24), ANCHOR, FIVE)
        for m in km.REVIEW_METRICS
    }
    return evaluate(KAFKA_BASELINES, series)


def _ids(breaches: list[Breach]) -> set[tuple[str, str]]:
    return {(b.rule.id, b.entity) for b in breaches}


@pytest.mark.parametrize(
    "cluster",
    [
        "healthy-steady",
        "healthy-busy",
        "healthy-rolling-restart",
        "healthy-weekly-pattern",
        "planted-isr-flapping",
        "planted-nightly-lag-benign",
        "planted-lag-growth",
    ],
)
async def test_no_breaches_where_kafka_is_within_normal_values(cluster: str) -> None:
    assert await _breaches(cluster) == []


async def test_under_min_isr_is_critical_and_still_active() -> None:
    found = await _breaches("planted-under-min-isr")
    assert ("under-min-isr", "kafka-1") in _ids(found) and ("failed-produce", "kafka-1") in _ids(found)
    first = found[0]
    assert first.rule.level == "critical" and first.active and first.worst == 3


async def test_handler_saturation_breaches_the_documented_normal_value_on_every_broker() -> None:
    found = await _breaches("planted-handler-saturation")
    idle = [b for b in found if b.rule.id == "handler-idle"]
    assert {b.entity for b in idle} == {"kafka-0", "kafka-1", "kafka-2"}
    assert all(b.worst < 0.3 and not b.active and b.rule.source.startswith("Kafka 3.8") for b in idle)


async def test_persistent_urp_breaches_and_disk_warning_fires() -> None:
    assert ("under-replicated", "kafka-0") in _ids(await _breaches("planted-injected-log"))
    disk = await _breaches("planted-disk-trend")
    assert _ids(disk) == {("disk-warning", "aks-kafka-pool-0002")}


def _series(values: list[float], label: str = "kafka-0", metric: str = km.URP) -> MetricSeries:
    return MetricSeries(metric, {"entity.name": label}, ANCHOR, FIVE, tuple(values))


def test_sustained_means_a_run_of_at_least_that_long() -> None:
    rule = BaselineRule("r", km.URP, "above", 0, "warning", "d", "s", timedelta(minutes=30))
    assert evaluate([rule], {km.URP: [_series([0] + [5] * 5 + [0])]}) == []  # 25 minutes
    hit = evaluate([rule], {km.URP: [_series([0] + [5] * 6 + [0])]})  # 30 minutes
    assert len(hit) == 1 and not hit[0].active and hit[0].first == ANCHOR + FIVE


def test_gaps_split_runs_and_the_last_point_sets_active() -> None:
    rule = BaselineRule("r", km.URP, "above", 0, "warning", "d", "s", timedelta(minutes=10))
    s = MetricSeries(km.URP, {"entity.name": "kafka-0"}, ANCHOR, FIVE, (1, 1, None, None, 2, 7, 3))
    [b] = evaluate([rule], {km.URP: [s]})
    assert b.first == ANCHOR and b.last == ANCHOR + 6 * FIVE and b.worst == 7 and b.active


def test_controller_check_works_on_the_cluster_sum() -> None:
    rule = next(r for r in KAFKA_BASELINES if r.id == "active-controller")
    ok = [
        _series([1] * 6, "kafka-0", km.ACTIVE_CONTROLLER),
        _series([0] * 6, "kafka-1", km.ACTIVE_CONTROLLER),
    ]
    assert evaluate([rule], {km.ACTIVE_CONTROLLER: ok}) == []
    lost = [
        _series([1, 0, 0, 0, 0, 1], "kafka-0", km.ACTIVE_CONTROLLER),
        _series([0] * 6, "kafka-1", km.ACTIVE_CONTROLLER),
    ]
    [b] = evaluate([rule], {km.ACTIVE_CONTROLLER: lost})
    assert b.entity == "cluster" and b.rule.level == "critical" and b.worst == 0 and not b.active


def test_critical_breaches_sort_first() -> None:
    warn = BaselineRule("a", km.URP, "above", 0, "warning", "d", "s")
    crit = BaselineRule("z", km.URP, "above", 0, "critical", "d", "s")
    found = evaluate([warn, crit], {km.URP: [_series([1, 1])]})
    assert [b.rule.id for b in found] == ["z", "a"]
