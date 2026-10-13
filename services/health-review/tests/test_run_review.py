from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from insights_testkit import ANCHOR, SCOPES, collector

from opstriage_contracts.tools import NotificationKind
from opstriage_insights.adapters.evidence_json import serialize
from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.adapters.llm_analyst import ScriptedAnalyst
from opstriage_insights.adapters.notifier import MemoryNotifier
from opstriage_insights.adapters.stores import MemoryReviewStore
from opstriage_insights.application.collect import BuildDigest, PrepareReview
from opstriage_insights.application.run_review import NotifyPolicy, RunReview
from opstriage_insights.domain.findings import AnalystOutput, Finding, Recommendation
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.ports.notifier import NotifyFailed

RID = UUID(int=9)
DONE = ANCHOR + timedelta(minutes=3)


def _run(store: MemoryReviewStore, analyst: Any, notifier: Any = None, **policy: Any) -> RunReview:
    clock = iter([ANCHOR, DONE])
    prepare = PrepareReview(
        store,
        collector(),
        BuildDigest(KeywordGuard()),
        RetentionPolicy(),
        lambda: next(clock),
        lambda: RID,
        serialize,
    )
    return RunReview(
        prepare,
        analyst,
        store,
        notifier,
        RetentionPolicy(),
        lambda: next(clock),
        notify=NotifyPolicy(**policy),
    )


OUTPUT = AnalystOutput(
    "Disk on aks-kafka-pool-0002 fills 1%/day.",
    (
        Finding(
            "potential_issue",
            "Disk full in ~11 days",
            "Retention growth",
            "high",
            "high",
            ("M1", "B1"),
            "about 11 days",
            "check per-topic size",
        ),
    ),
    (Recommendation("Review retention.ms on large topics", "frees disk before 90%", "low", ("M1",), (0,)),),
)


async def test_completed_review_stores_report_shortens_evidence_and_sends_a_card() -> None:
    store, notifier = MemoryReviewStore(), MemoryNotifier()
    outcome = await _run(store, ScriptedAnalyst(OUTPUT), notifier)(SCOPES["planted-disk-trend"])
    report = outcome.report
    assert report["model"] == "scripted" and report["analyst_available"] and report["worst_risk"] == "high"
    assert [f["source"] for f in report["findings"]] == ["model"]  # B1 cited, so no duplicate rule finding
    assert report["findings"][0]["forecasts"] and report["recommendations"][0]["addresses"] == [0]
    assert {e["id"] for e in report["evidence"]} >= {"B1", "M1"}
    assert next(e for e in report["evidence"] if e["id"] == "M1")["sparkline"]
    review = store.reviews[RID]
    assert review["status"] == "completed" and review["expires_at"] == DONE + timedelta(hours=48)
    assert all(e["expires_at"] == DONE + timedelta(hours=4) for e in store.evidence)
    assert store.reports[RID]["markdown"].startswith("# Kafka health review: planted-disk-trend")
    [card] = notifier.sent
    assert card.kind is NotificationKind.HEALTH_REVIEW and card.channels == ("teams",)
    assert card.links["Report"].endswith(f"/ui/reports/{RID}") and "Disk full" in card.summary
    assert outcome.notified


async def test_analyst_down_still_completes_with_rule_findings() -> None:
    store = MemoryReviewStore()
    outcome = await _run(store, ScriptedAnalyst(fail=True))(SCOPES["planted-under-min-isr"])
    assert not outcome.report["analyst_available"] and outcome.report["model"] is None
    assert outcome.report["findings"] and all(f["source"] == "rule" for f in outcome.report["findings"])
    assert store.reviews[RID]["status"] == "completed"


async def test_provider_none_writes_rules_only_reports() -> None:
    outcome = await _run(MemoryReviewStore(), None)(SCOPES["healthy-steady"])
    assert outcome.report["findings"] == [] and outcome.report["worst_risk"] is None
    assert "disabled" in outcome.report["notes"][0]


async def test_card_threshold_and_notifier_failure_do_not_fail_the_review() -> None:
    class Broken:
        async def send(self, request: Any) -> None:
            raise NotifyFailed("down")

    outcome = await _run(MemoryReviewStore(), ScriptedAnalyst(OUTPUT), Broken())(SCOPES["planted-disk-trend"])
    assert not outcome.notified
    quiet = MemoryNotifier()
    await _run(MemoryReviewStore(), ScriptedAnalyst(OUTPUT), quiet, min_risk="critical")(
        SCOPES["planted-disk-trend"]
    )
    assert quiet.sent == []


async def test_unexpected_errors_mark_the_review_failed() -> None:
    class Exploding:
        async def analyse(self, *_: Any) -> Any:
            raise RuntimeError("bug")

    store = MemoryReviewStore()
    with pytest.raises(RuntimeError):
        await _run(store, Exploding())(SCOPES["healthy-steady"])
    assert store.reviews[RID]["status"] == "failed"


async def test_the_analyst_sees_the_rendered_digest_without_injected_text() -> None:
    analyst = ScriptedAnalyst(OUTPUT)
    await _run(MemoryReviewStore(), analyst)(SCOPES["planted-injected-log"])
    [prompt] = analyst.calls
    assert prompt.startswith("CLUSTER planted-injected-log") and "previous instructions" not in prompt.lower()
