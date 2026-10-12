from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from opstriage_poller.ports import Clock, CursorStore, IntakeClient, LeaderLock, ProblemSource

log = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class PollReport:
    skipped: bool
    fetched: int = 0
    accepted: int = 0
    duplicates: int = 0


class PollProblems:
    def __init__(
        self,
        source: ProblemSource,
        cursors: CursorStore,
        intake: IntakeClient,
        lock: LeaderLock,
        clock: Clock,
        source_name: str,
        lookback: timedelta,
        sweep_interval: timedelta,
    ) -> None:
        self._source, self._cursors, self._intake, self._lock = source, cursors, intake, lock
        self._clock, self._name, self._lookback = clock, source_name, lookback
        self._sweep_interval = sweep_interval

    async def run_once(self) -> PollReport:
        async with self._lock.try_acquire() as acquired:
            if not acquired:
                return PollReport(skipped=True)
            started = self._clock.now()
            cursor = await self._cursors.get(self._name)
            hwm = cursor.high_water_mark if cursor else started - self._lookback
            events = await self._source.fetch_changed(since=hwm, lookback=self._lookback)
            sweep_due = (
                cursor is None
                or cursor.last_sweep_at is None
                or (started - cursor.last_sweep_at >= self._sweep_interval)
            )
            if sweep_due:
                events += await self._source.fetch_active()

            accepted = duplicates = 0
            newest: datetime = hwm
            for event in sorted(events, key=lambda e: e.observed_at):
                outcome = await self._intake.submit(event)  # raises on 4xx/5xx after retries
                accepted += outcome == "accepted"
                duplicates += outcome == "duplicate"
                newest = max(newest, event.observed_at)
            await self._cursors.set(  # only after the whole batch succeeded
                self._name,
                high_water_mark=newest,
                last_sweep_at=started if sweep_due else (cursor.last_sweep_at if cursor else None),
            )
            log.info(
                "poll_cycle", source=self._name, fetched=len(events), accepted=accepted, duplicates=duplicates
            )
            return PollReport(skipped=False, fetched=len(events), accepted=accepted, duplicates=duplicates)
