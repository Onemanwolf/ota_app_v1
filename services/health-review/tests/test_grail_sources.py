import json
from datetime import timedelta
from typing import Any

import pytest
from insights_testkit import ANCHOR, STORE

from opstriage_insights.adapters.grail_sources import (
    GrailLogSource,
    GrailMetricSource,
    GrailProblemSource,
    logs_dql,
    metric_dql,
    problems_dql,
)
from opstriage_insights.domain.model import ClusterScope

SCOPE = ClusterScope("c1", "streaming", ("kafka",), ("PROCESS_GROUP_INSTANCE-1", 'X") or true or ("'))


class FakeGrail:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows, self.queries = rows, []

    async def query(self, dql: str, *, max_records: int = 5_000) -> list[dict[str, Any]]:
        self.queries.append(dql)
        return self.rows


def test_logs_dql_filters_scope_first_and_quotes_values() -> None:
    dql = logs_dql(SCOPE, ANCHOR - timedelta(hours=24), ANCHOR, 500)
    assert dql.startswith('fetch logs, from: "2026-10-11T06:00:00Z", to: "2026-10-12T06:00:00Z"')
    assert 'in(status, {"ERROR", "WARN"})' in dql and 'k8s.namespace.name == "streaming"' in dql
    assert json.dumps('X") or true or ("') in dql  # escaped as a string literal, not DQL syntax
    assert dql.rstrip().endswith("| limit 500")


def test_metric_dql_uses_the_right_dimensions_and_rejects_odd_keys() -> None:
    broker = metric_dql(
        SCOPE, "kafka.server.ReplicaManager.UnderReplicatedPartitions", ANCHOR, ANCHOR, timedelta(minutes=5)
    )
    assert (
        "by: {dt.entity.process_group_instance}, interval: 5m" in broker and "filter in(dt.entity" in broker
    )
    lag = metric_dql(
        SCOPE, "kafka.consumer.consumer-metrics.records-lag-max", ANCHOR, ANCHOR, timedelta(hours=1)
    )
    assert "by: {`client-id`, topic}, interval: 1h" in lag and "filter in(dt.entity" not in lag
    assert "by: {dt.entity.host}" in metric_dql(
        SCOPE, "dt.host.disk.used.percent", ANCHOR, ANCHOR, timedelta(hours=1)
    )
    with pytest.raises(ValueError, match="unexpected metric key"):
        metric_dql(SCOPE, "x) | fetch logs", ANCHOR, ANCHOR, timedelta(hours=1))


def test_problems_dql_excludes_duplicates() -> None:
    dql = problems_dql(SCOPE, ANCHOR - timedelta(days=7), ANCHOR)
    assert "not(dt.davis.is_duplicate)" in dql and "PROCESS_GROUP_INSTANCE-1" in dql


async def test_adapters_map_the_raw_fixture_records() -> None:
    raw_logs = STORE.raw("planted-fd-limit", "logs")
    logs = await GrailLogSource(FakeGrail(raw_logs)).fetch(SCOPE, ANCHOR, ANCHOR, 10)  # type: ignore[arg-type]
    assert len(logs) == len(raw_logs) and logs[0].status == "ERROR"
    raw_series = STORE.raw("planted-fd-limit", "metrics")[
        "kafka.server.ReplicaManager.UnderReplicatedPartitions"
    ]["5m"]
    series = await GrailMetricSource(FakeGrail(raw_series)).fetch(  # type: ignore[arg-type]
        SCOPE, "kafka.server.ReplicaManager.UnderReplicatedPartitions", ANCHOR, ANCHOR, timedelta(minutes=5)
    )
    assert {s.label for s in series} == {"kafka-0", "kafka-1", "kafka-2"}
    raw_problems = STORE.raw("planted-controller-elections", "problems")
    problems = await GrailProblemSource(FakeGrail(raw_problems)).fetch(SCOPE, ANCHOR, ANCHOR)  # type: ignore[arg-type]
    assert len(problems) == 7
    assert await GrailProblemSource(FakeGrail(raw_problems)).fetch(ClusterScope("x"), ANCHOR, ANCHOR) == []  # type: ignore[arg-type]
