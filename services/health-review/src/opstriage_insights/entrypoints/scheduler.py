"""Background loops (OTA-APP-002 3.3 step 1 and section 4).

The purge every interval, and the daily review of the clusters in INSIGHTS__SCHEDULE__CLUSTERS at
INSIGHTS__SCHEDULE__DAILY_AT in its time zone."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog

from opstriage_insights.application.purge import Purge
from opstriage_insights.domain.model import ClusterScope
from opstriage_insights.entrypoints.api import Runner

log = structlog.get_logger()


class Scheduler:
    def __init__(
        self,
        purge: Purge,
        runner: Runner,
        clusters: Sequence[ClusterScope],
        *,
        purge_interval: timedelta,
        daily_at: time,
        tz: ZoneInfo,
        clock: Callable[[], datetime],
    ) -> None:
        self._purge, self._runner, self._clusters = purge, runner, tuple(clusters)
        self._interval, self._at, self._tz, self._clock = purge_interval, daily_at, tz, clock
        self._tasks: list[asyncio.Task[None]] = []
        self._last_daily: date | None = None

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._purge_loop(), name="purge"),
            asyncio.create_task(self._daily_loop(), name="daily-reviews"),
        ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def purge_once(self) -> None:
        try:
            counts = await self._purge()
            log.info(
                "purge_completed",
                evidence_deleted=counts.evidence_deleted,
                reports_deleted=counts.reports_deleted,
                reviews_expired=counts.reviews_expired,
                reviews_deleted=counts.reviews_deleted,
            )
        except Exception as exc:  # keep the loop alive; the next run retries
            log.warning("purge_failed", error=type(exc).__name__)

    def run_daily_if_due(self) -> list[str]:
        """Start today's reviews once, at or after the configured time. Returns the clusters started."""
        local = self._clock().astimezone(self._tz)
        if not self._clusters or local.time() < self._at or self._last_daily == local.date():
            return []
        self._last_daily = local.date()
        for scope in self._clusters:
            self._runner.start(scope)
        started = [c.name for c in self._clusters]
        log.info("daily_reviews_started", clusters=len(started))
        return started

    async def _purge_loop(self) -> None:
        while True:
            await self.purge_once()
            await asyncio.sleep(self._interval.total_seconds())

    async def _daily_loop(self) -> None:
        if not self._clusters:
            return
        self._last_daily = (
            self._clock().astimezone(self._tz).date()
            if (self._clock().astimezone(self._tz).time() >= self._at)
            else None
        )  # no catch-up run at start-up
        while True:
            self.run_daily_if_due()
            await asyncio.sleep(60)
