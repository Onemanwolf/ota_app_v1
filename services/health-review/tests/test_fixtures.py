"""The health-review fixtures load through the fake adapters and match what the generator builds (5.2 DoD)."""

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from opstriage_insights.adapters.fixtures import (
    FixtureLogSource,
    FixtureMetricSource,
    FixtureNotFound,
    FixtureProblemSource,
    FixtureStore,
)
from opstriage_insights.domain.model import ClusterScope

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "insights"
ANCHOR = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
STORE = FixtureStore(FIXTURES, lambda: ANCHOR, shift_to_now=False)
CLUSTERS = STORE.clusters()


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("insights_fixture_generator", FIXTURES / "generate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sixteen_clusters_twelve_planted_four_healthy() -> None:
    kinds = [STORE.scenario(c)["kind"] for c in CLUSTERS]
    assert len(CLUSTERS) == 16 and kinds.count("planted") == 12 and kinds.count("healthy") == 4


def test_committed_fixtures_match_the_generator(tmp_path: Path) -> None:
    _generator().main(tmp_path)
    built = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*.json"))
    committed = sorted(p.relative_to(FIXTURES) for p in FIXTURES.rglob("*.json"))
    assert built == committed
    stale = [str(p) for p in built if (tmp_path / p).read_bytes() != (FIXTURES / p).read_bytes()]
    assert stale == [], "run: uv run python fixtures/insights/generate.py"


@pytest.mark.parametrize("cluster", CLUSTERS)
async def test_every_cluster_loads_through_the_fakes(cluster: str) -> None:
    scope = ClusterScope(name=cluster)
    logs = await FixtureLogSource(STORE).fetch(scope, ANCHOR - timedelta(hours=24), ANCHOR, 50_000)
    assert logs and all(r.status in {"ERROR", "WARN"} for r in logs)
    assert [r.timestamp for r in logs] == sorted((r.timestamp for r in logs), reverse=True)
    metrics = FixtureMetricSource(STORE)
    day = await metrics.fetch(
        scope,
        "kafka.server.ReplicaManager.UnderReplicatedPartitions",
        ANCHOR - timedelta(hours=24),
        ANCHOR,
        timedelta(minutes=5),
    )
    month = await metrics.fetch(
        scope, "dt.host.disk.used.percent", ANCHOR - timedelta(days=30), ANCHOR, timedelta(hours=1)
    )
    assert {s.label for s in day} == {"kafka-0", "kafka-1", "kafka-2"}
    assert all(len(s.values) == 288 and s.end == ANCHOR for s in day)
    assert all(len(s.values) == 720 for s in month)
    problems = await FixtureProblemSource(STORE).fetch(scope, ANCHOR - timedelta(days=7), ANCHOR)
    assert all(not p.is_duplicate for p in problems)


@pytest.mark.parametrize("cluster", CLUSTERS)
async def test_expectations_point_at_evidence_that_exists(cluster: str) -> None:
    scenario = STORE.scenario(cluster)
    scope = ClusterScope(name=cluster)
    logs = await FixtureLogSource(STORE).fetch(scope, ANCHOR - timedelta(hours=24), ANCHOR, 50_000)
    raw_metrics = STORE.raw(cluster, "metrics")
    assert scenario["expect"] or scenario["forbid"], (
        "a cluster must state what a review should or must not say"
    )
    for e in scenario["expect"]:
        ev = e["evidence"]
        if "metric" in ev:
            labels = {r["entity.name"] for r in raw_metrics[ev["metric"]]["1h"]}
            assert ev["entity"] in labels, (ev, labels)
        if "log" in ev:
            assert any(ev["log"] in r.content for r in logs), ev
        assert e["min_risk"] in {"low", "medium", "high", "critical"}
        assert e["kind"] in {"trend", "potential_issue", "current_issue", "root_cause_hypothesis"}
    if scenario["kind"] == "healthy":
        assert scenario["expect"] == [] and scenario["forbid"]["max_risk"] == "low"


async def test_windows_crop_series_and_shift_moves_everything_to_now() -> None:
    now = ANCHOR + timedelta(days=3, minutes=7)
    shifted = FixtureStore(FIXTURES, lambda: now, shift_to_now=True)
    scope = ClusterScope(name="planted-disk-trend")
    series = await FixtureMetricSource(shifted).fetch(
        scope, "dt.host.disk.used.percent", now - timedelta(days=7), now, timedelta(hours=1)
    )
    assert all(len(s.values) == 168 and s.end == now for s in series)
    logs = await FixtureLogSource(shifted).fetch(scope, now - timedelta(hours=24), now, 50_000)
    raw = json.loads((FIXTURES / "planted-disk-trend" / "logs.json").read_text())
    assert len(logs) == len(raw) and logs[0].timestamp > ANCHOR + timedelta(days=2)


async def test_log_limit_keeps_the_newest_records() -> None:
    scope = ClusterScope(name="planted-fd-limit")
    logs = await FixtureLogSource(STORE).fetch(scope, ANCHOR - timedelta(hours=24), ANCHOR, 5)
    everything = await FixtureLogSource(STORE).fetch(scope, ANCHOR - timedelta(hours=24), ANCHOR, 50_000)
    assert logs == everything[:5]


@pytest.mark.parametrize("bad", ["", "../etc", "a/b", "no-such-cluster"])
def test_unknown_or_unsafe_cluster_names_are_rejected(bad: str) -> None:
    with pytest.raises(FixtureNotFound):
        STORE.scenario(bad)


async def test_unsupported_interval_is_an_error() -> None:
    with pytest.raises(ValueError, match="5m and 1h"):
        await FixtureMetricSource(STORE).fetch(
            ClusterScope(name="healthy-steady"),
            "x",
            ANCHOR - timedelta(hours=1),
            ANCHOR,
            timedelta(minutes=1),
        )
