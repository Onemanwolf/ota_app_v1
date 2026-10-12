from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_poller.application.poll_problems import PollProblems
from opstriage_poller.domain.cursor import Cursor
from opstriage_poller.ports import IntakeRejected

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def event(pid: str, observed: datetime, status: str = "ACTIVE") -> ProblemEvent:
    return ProblemEvent(
        source=AlertSource.DYNATRACE_POLLER_GRAIL,
        problem_id=pid,
        status=ProblemStatus(status),
        title="t",
        started_at=T0,
        observed_at=observed,
    )


class Source:
    def __init__(self, changed: list[ProblemEvent], active: list[ProblemEvent]) -> None:
        self.changed, self.active = changed, active
        self.since: list[datetime] = []
        self.active_calls = 0

    async def fetch_changed(self, since: datetime, lookback: timedelta) -> list[ProblemEvent]:
        self.since.append(since)
        return [e for e in self.changed if e.observed_at > since]

    async def fetch_active(self) -> list[ProblemEvent]:
        self.active_calls += 1
        return list(self.active)


class Cursors:
    def __init__(self) -> None:
        self.cursor: Cursor | None = None

    async def get(self, source: str) -> Cursor | None:
        return self.cursor

    async def set(self, source: str, *, high_water_mark: datetime, last_sweep_at: datetime | None) -> None:
        self.cursor = Cursor(source, high_water_mark, last_sweep_at)


class Intake:
    def __init__(self, fail_on: str | None = None) -> None:
        self.seen: set[str] = set()
        self.fail_on = fail_on

    async def submit(self, e: ProblemEvent) -> Literal["accepted", "duplicate"]:
        if e.problem_id == self.fail_on:
            raise IntakeRejected("422")
        if e.dedup_key in self.seen:
            return "duplicate"
        self.seen.add(e.dedup_key)
        return "accepted"


class Lock:
    def __init__(self, free: bool = True) -> None:
        self.free = free

    @asynccontextmanager
    async def try_acquire(self) -> AsyncIterator[bool]:
        yield self.free


class Clock:
    def __init__(self) -> None:
        self.current = T0

    def now(self) -> datetime:
        return self.current


def poller(
    source: Source, cursors: Cursors, intake: Intake, clock: Clock, lock: Lock | None = None
) -> PollProblems:
    return PollProblems(
        source, cursors, intake, lock or Lock(), clock, "fake", timedelta(hours=7), timedelta(minutes=10)
    )


async def test_first_cycle_uses_lookback_sweeps_and_advances_cursor() -> None:
    src = Source([event("a", T0 - timedelta(minutes=5))], [event("a", T0 - timedelta(minutes=5))])
    cursors, clock = Cursors(), Clock()
    report = await poller(src, cursors, Intake(), clock).run_once()
    assert src.since == [T0 - timedelta(hours=7)]
    assert (report.fetched, report.accepted, report.duplicates) == (2, 1, 1)
    assert cursors.cursor is not None and cursors.cursor.high_water_mark == T0 - timedelta(minutes=5)
    assert cursors.cursor.last_sweep_at == T0


async def test_cursor_does_not_advance_when_intake_fails() -> None:
    src = Source([event("ok", T0 - timedelta(minutes=2)), event("bad", T0 - timedelta(minutes=1))], [])
    cursors = Cursors()
    with pytest.raises(IntakeRejected):
        await poller(src, cursors, Intake(fail_on="bad"), Clock()).run_once()
    assert cursors.cursor is None


async def test_active_sweep_cadence() -> None:
    src, cursors, clock, intake = Source([], [event("a", T0)]), Cursors(), Clock(), Intake()
    p = poller(src, cursors, intake, clock)
    await p.run_once()  # no cursor yet: sweep
    clock.current += timedelta(minutes=5)
    await p.run_once()  # 5 min since the sweep: not due
    clock.current += timedelta(minutes=5)
    report = await p.run_once()  # 10 min: due again, the intake answers duplicate
    assert src.active_calls == 2 and report.duplicates == 1


async def test_skips_the_cycle_when_another_replica_holds_the_lock() -> None:
    src = Source([event("a", T0)], [])
    report = await poller(src, Cursors(), Intake(), Clock(), Lock(free=False)).run_once()
    assert report.skipped and src.since == []
