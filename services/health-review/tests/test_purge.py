"""Retention enforcement: evidence at completion + 4 h, reports at 48 h, tombstones answer 410 then go (5.8)."""

from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from insights_testkit import ANCHOR, SCOPES, collector

from opstriage_insights.adapters.evidence_json import serialize
from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.adapters.llm_analyst import ScriptedAnalyst
from opstriage_insights.adapters.stores import MemoryReviewStore
from opstriage_insights.application.collect import BuildDigest, PrepareReview
from opstriage_insights.application.purge import Purge
from opstriage_insights.application.run_review import RunReview
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.entrypoints.api import Runner
from opstriage_insights.entrypoints.scheduler import Scheduler

RID = UUID(int=21)
H = timedelta(hours=1)


async def _completed(store: MemoryReviewStore) -> datetime:
    done = ANCHOR + timedelta(minutes=4)
    clock = iter([ANCHOR, done])
    prepare = PrepareReview(
        store,
        collector(),
        BuildDigest(KeywordGuard()),
        RetentionPolicy(),
        lambda: next(clock),
        lambda: RID,
        serialize,
    )
    await RunReview(prepare, ScriptedAnalyst(), store, None, RetentionPolicy(), lambda: next(clock))(
        SCOPES["planted-disk-trend"], {"display_id": "P-1"}
    )
    return done


async def test_retention_timeline_end_to_end() -> None:
    store = MemoryReviewStore()
    done = await _completed(store)
    purge = Purge(store, lambda: done)
    assert len(store.evidence) == 3

    first = await purge(done + 3 * H)  # nothing has expired yet
    assert (first.evidence_deleted, first.reports_deleted) == (0, 0) and len(store.evidence) == 3

    second = await purge(done + 4 * H)  # evidence: completion + 4 h
    assert second.evidence_deleted == 3 and store.evidence == []
    assert (await store.get_report(RID, done + 5 * H)).report is not None  # type: ignore[union-attr]

    third = await purge(done + 48 * H)  # report: completion + 48 h; the review becomes a tombstone
    assert third.reports_deleted == 1 and third.reviews_expired == 1 and RID not in store.reports
    tomb = store.reviews[RID]
    assert tomb["subject"] is None and tomb.get("error") is None  # no content survives
    gone = await store.get_report(RID, done + 49 * H)
    assert gone is not None and gone.expired and gone.report is None  # links answer 410

    fourth = await purge(done + 48 * H + timedelta(days=30))  # tombstone removed after 30 days
    assert fourth.reviews_deleted == 1 and await store.get_report(RID, done + timedelta(days=40)) is None
    assert [p["evidence_deleted"] for p in store.purge_log] == [0, 3, 0, 0]


async def test_expire_review_makes_everything_expire_now() -> None:
    store = MemoryReviewStore()
    done = await _completed(store)
    assert await store.expire_review(RID, done) and not await store.expire_review(UUID(int=999), done)
    counts = await Purge(store, lambda: done)()
    assert counts.evidence_deleted == 3 and counts.reports_deleted == 1
    assert (await store.get_report(RID, done)).expired  # type: ignore[union-attr]


def test_daily_reviews_run_once_a_day_at_or_after_the_time() -> None:
    started: list[str] = []

    class FakeRunner:
        def start(self, scope: Any, *a: Any) -> UUID:
            started.append(scope.name)
            return UUID(int=1)

    now = [datetime(2026, 10, 13, 5, 59, tzinfo=UTC)]
    s = Scheduler(
        Purge(MemoryReviewStore(), lambda: now[0]),
        FakeRunner(),  # type: ignore[arg-type]
        [SCOPES["healthy-steady"]],
        purge_interval=timedelta(minutes=15),
        daily_at=time(6, 0),
        tz=ZoneInfo("UTC"),
        clock=lambda: now[0],
    )
    assert s.run_daily_if_due() == []  # 05:59, too early
    now[0] = now[0].replace(minute=0, hour=6)
    assert s.run_daily_if_due() == ["healthy-steady"]
    now[0] += timedelta(hours=3)
    assert s.run_daily_if_due() == []  # once per day
    now[0] += timedelta(days=1)
    assert s.run_daily_if_due() == ["healthy-steady"] and started == ["healthy-steady", "healthy-steady"]


def test_no_clusters_means_no_scheduled_reviews() -> None:
    s = Scheduler(
        Purge(MemoryReviewStore(), lambda: ANCHOR),
        Runner(None),
        [],  # type: ignore[arg-type]
        purge_interval=timedelta(minutes=15),
        daily_at=time(0, 0),
        tz=ZoneInfo("UTC"),
        clock=lambda: ANCHOR,
    )
    assert s.run_daily_if_due() == []
