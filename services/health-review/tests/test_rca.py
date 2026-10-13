"""Root-cause analysis: window planning, cluster resolution and the code-computed timeline (5.6)."""

from datetime import timedelta
from typing import Any

import pytest
from insights_testkit import ANCHOR, SCOPES, STORE, collector

from opstriage_insights.adapters.fixtures import FixtureProblemSource
from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.application.collect import BuildDigest
from opstriage_insights.application.run_rca import PlanRca, RcaError, resolve_cluster
from opstriage_insights.domain.digest import Digest
from opstriage_insights.domain.model import ClusterScope, ProblemRecord

RCA = [c for c in sorted(SCOPES) if STORE.scenario(c)["kind"] == "rca"]
PLAN = PlanRca(FixtureProblemSource(STORE), dict(SCOPES), lambda: ANCHOR)


async def _rca_digest(cluster: str) -> Digest:
    plan = await PLAN(STORE.scenario(cluster)["rca"]["display_id"])
    evidence = await collector()(plan.scope, ANCHOR, plan.window)
    return await BuildDigest(KeywordGuard())(evidence)


def _first(d: Digest, ev: dict[str, str]) -> Any:
    """Earliest timeline time for this evidence (metric departure or new log template)."""
    times = []
    for item in d.timeline:
        if item.cid.startswith("M") and "metric" in ev:
            m = next(m for m in d.metrics if m.cid == item.cid)
            if m.metric == ev["metric"] and ev["entity"] in ("*", m.entity):
                times.append(item.at)
        if item.cid.startswith("L") and "log" in ev:
            le = next(e for e in d.logs if e.cid == item.cid)
            if not le.withheld and (ev["log"] in le.template or any(ev["log"] in x for x in le.examples)):
                times.append(item.at)
    return min(times, default=None)


async def test_plan_finds_the_problem_its_cluster_and_a_window_two_hours_before() -> None:
    plan = await PLAN("P-26101201")
    assert plan.scope.name == "rca-disk-full"
    start, end = plan.window
    assert end == ANCHOR and (end - start) == timedelta(
        hours=5
    )  # active problem: 2 h before its start until now
    assert plan.subject["display_id"] == "P-26101201" and plan.subject["title"] == "Process unavailable"


async def test_closed_problem_window_ends_when_it_closed() -> None:
    plan = await PLAN("P-26101203")  # closed after 40 minutes
    assert plan.window[1] - plan.window[0] == timedelta(hours=2, minutes=40)


@pytest.mark.parametrize(("display_id", "status"), [("P-99999999", 404)])
async def test_unknown_problem(display_id: str, status: int) -> None:
    with pytest.raises(RcaError) as err:
        await PLAN(display_id)
    assert err.value.status == status


def test_cluster_resolution_rules() -> None:
    p = ProblemRecord("1", "P-1", "t", None, "ACTIVE", ANCHOR, None, ("E1",))
    a, b = ClusterScope("a", entity_ids=("E1",)), ClusterScope("b", entity_ids=("E1", "E2"))
    assert resolve_cluster(p, {"a": a}, None, None) is a
    assert resolve_cluster(p, {"a": a, "b": b}, "b", None) is b
    assert resolve_cluster(p, {"a": a, "b": b}, None, "a") is a
    for clusters, hint, requested, status in [
        ({"a": a, "b": b}, None, None, 409),
        ({}, None, None, 422),
        ({"a": a}, None, "zzz", 404),
    ]:
        with pytest.raises(RcaError) as err:
            resolve_cluster(p, clusters, hint, requested)
        assert err.value.status == status


@pytest.mark.parametrize("cluster", RCA)
async def test_timeline_puts_the_planted_cause_before_its_effects(cluster: str) -> None:
    d = await _rca_digest(cluster)
    rca = STORE.scenario(cluster)["rca"]
    cause = min((t for t in (_first(d, ev) for ev in rca["root_cause"]) if t), default=None)
    assert cause is not None, f"{cluster}: cause not on the timeline:\n{d.render()}"
    for ev in rca["effects"]:
        effect = _first(d, ev)
        assert effect is None or cause <= effect, (cluster, ev, cause, effect)
    assert any(i.what == "problem started" for i in d.timeline)
    assert "ROOT-CAUSE WINDOW" in d.render() and d.tokens <= 12_000


async def test_review_digests_have_no_timeline() -> None:
    evidence = await collector()(SCOPES["healthy-steady"], ANCHOR)
    d = await BuildDigest(KeywordGuard())(evidence)
    assert d.window is None and d.timeline == () and "ROOT-CAUSE WINDOW" not in d.render()
