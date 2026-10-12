from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Literal, Protocol

from opstriage_contracts.problems import ProblemEvent
from opstriage_poller.domain.cursor import Cursor
from opstriage_shared.clock import Clock

__all__ = ["Clock", "CursorStore", "IntakeClient", "IntakeRejected", "LeaderLock", "ProblemSource"]


class IntakeRejected(RuntimeError):
    """The intake answered 4xx: a contract bug. The cursor must not advance."""


class ProblemSource(Protocol):
    async def fetch_changed(self, since: datetime, lookback: timedelta) -> list[ProblemEvent]: ...

    async def fetch_active(self) -> list[ProblemEvent]: ...


class CursorStore(Protocol):
    async def get(self, source: str) -> Cursor | None: ...

    async def set(
        self, source: str, *, high_water_mark: datetime, last_sweep_at: datetime | None
    ) -> None: ...


class IntakeClient(Protocol):
    async def submit(self, event: ProblemEvent) -> Literal["accepted", "duplicate"]: ...


class LeaderLock(Protocol):
    def try_acquire(self) -> AbstractAsyncContextManager[bool]: ...
