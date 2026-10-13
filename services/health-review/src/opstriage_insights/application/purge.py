"""Retention enforcement (OTA-APP-002 section 4): runs every INSIGHTS__RETENTION__PURGE_INTERVAL."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from opstriage_insights.ports.store import PurgeCounts, ReviewStore

TOMBSTONE = timedelta(days=30)  # expired reviews keep a content-free row so links answer 410, then go


class Purge:
    def __init__(
        self, store: ReviewStore, clock: Callable[[], datetime], tombstone: timedelta = TOMBSTONE
    ) -> None:
        self._store, self._clock, self._tombstone = store, clock, tombstone

    async def __call__(self, now: datetime | None = None) -> PurgeCounts:
        return await self._store.purge(now or self._clock(), self._tombstone)
