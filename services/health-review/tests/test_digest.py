"""Digest snapshots, budget and coverage of every planted signal (5.4 DoD)."""

import os
from typing import Any

import pytest
from insights_testkit import ANCHOR, SCOPES, STORE, collector

from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.application.collect import BuildDigest
from opstriage_insights.domain.digest import Digest, fit

SNAPSHOTS = os.path.join(os.path.dirname(__file__), "snapshots")
SNAPSHOT_CLUSTERS = [
    "planted-disk-trend",
    "planted-under-min-isr",
    "planted-injected-log",
    "healthy-rolling-restart",
]


async def _digest(cluster: str, *, guard: Any = None, max_tokens: int = 12_000) -> Digest:
    evidence = await collector()(SCOPES[cluster], ANCHOR)
    return await BuildDigest(guard or KeywordGuard(), max_tokens=max_tokens)(evidence)


def _check_snapshot(cluster: str, text: str) -> None:
    path = os.path.join(SNAPSHOTS, f"digest_{cluster}.txt")
    if os.environ.get("UPDATE_SNAPSHOTS") or not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    with open(path, encoding="utf-8") as fh:
        assert text == fh.read(), "digest changed; review and rerun with UPDATE_SNAPSHOTS=1"


@pytest.mark.parametrize("cluster", SNAPSHOT_CLUSTERS)
async def test_digest_matches_snapshot(cluster: str) -> None:
    _check_snapshot(cluster, (await _digest(cluster)).render())


@pytest.mark.parametrize("cluster", sorted(SCOPES))
async def test_every_cluster_fits_the_default_budget_untrimmed(cluster: str) -> None:
    d = await _digest(cluster)
    assert d.tokens <= 12_000 and not d.dropped
    assert d.citation_ids() == {c for c in d.citation_ids() if c[0] in "MLPB"}


@pytest.mark.parametrize("cluster", [c for c in sorted(SCOPES) if STORE.scenario(c)["expect"]])
async def test_every_planted_signal_is_in_the_digest(cluster: str) -> None:
    d = await _digest(cluster)
    metrics = {(m.metric, m.entity) for m in d.metrics}
    for e in STORE.scenario(cluster)["expect"]:
        ev = e["evidence"]
        if "metric" in ev:
            assert (ev["metric"], ev["entity"]) in metrics, (cluster, ev)
        if "log" in ev:
            assert any(
                ev["log"] in le.template or any(ev["log"] in x for x in le.examples)
                for le in d.logs
                if not le.withheld
            ), (cluster, ev)


@pytest.mark.parametrize("cluster", [c for c in sorted(SCOPES) if STORE.scenario(c)["kind"] == "healthy"])
async def test_healthy_clusters_have_no_breaches(cluster: str) -> None:
    assert (await _digest(cluster)).breaches == ()


async def test_injected_text_never_reaches_the_digest() -> None:
    d = await _digest("planted-injected-log")
    text = d.render()
    assert d.guard_withheld == 1 and "previous instructions" not in text.lower()
    assert "[withheld: flagged by the injection guard]" in text
    assert any(b.breach.rule.id == "under-replicated" for b in d.breaches)  # the real issue is still there


async def test_guard_down_fails_closed() -> None:
    d = await _digest("planted-fd-limit", guard=KeywordGuard(available=False))
    text = d.render()
    assert not d.guard_available and all(le.withheld for le in d.logs)
    assert "Too many open files" not in text and "injection guard was unavailable" in text


async def test_breaches_cite_metric_entries_that_exist() -> None:
    d = await _digest("planted-handler-saturation")
    ids = d.citation_ids()
    assert d.breaches and all(b.cites and set(b.cites) <= ids for b in d.breaches)


async def test_a_tight_budget_trims_low_signal_content_but_keeps_breaches() -> None:
    full = await _digest("planted-under-min-isr")
    tight = fit(full, 700)
    assert tight.tokens < full.tokens and tight.dropped
    assert tight.breaches == full.breaches
    kept = {m.cid for m in tight.metrics}
    assert all(set(b.cites) <= kept for b in tight.breaches)
    assert "TRIMMED to fit the budget" in tight.render()
