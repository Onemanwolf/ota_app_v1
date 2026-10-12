"""Composition root for the poller."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import httpx2

from opstriage_poller.adapters.classic import ClassicProblemSource
from opstriage_poller.adapters.fixture_source import FixtureProblemSource
from opstriage_poller.adapters.grail import DynatraceOAuth, GrailClient, GrailProblemSource
from opstriage_poller.adapters.intake_http import HttpIntakeClient
from opstriage_poller.adapters.postgres import PostgresCursorStore, PostgresLeaderLock
from opstriage_poller.application.poll_problems import PollProblems
from opstriage_poller.ports import ProblemSource
from opstriage_poller.settings import Settings
from opstriage_shared.clock import SystemClock
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


@dataclass(slots=True)
class Container:
    pool: Pool
    http: httpx2.AsyncClient
    poll: PollProblems

    async def close(self) -> None:
        await self.http.aclose()
        await self.pool.close()


async def build_container(settings: Settings) -> Container:
    p = settings.poller
    pool = await open_pool(settings.db)
    await migrate(pool, "poller", MIGRATIONS)
    http = httpx2.AsyncClient(timeout=httpx2.Timeout(p.http_timeout_seconds))
    clock = SystemClock()
    lookback = timedelta(hours=p.lookback_hours)
    source: ProblemSource
    if p.source == "grail":
        source = GrailProblemSource(GrailClient(http, DynatraceOAuth(http, p.grail), p.grail), lookback)
    elif p.source == "classic":
        source = ClassicProblemSource(http, p.classic, lookback, clock)
    else:
        source = FixtureProblemSource(p.fake_fixtures_dir, loaded_at=clock.now())
    poll = PollProblems(
        source=source,
        cursors=PostgresCursorStore(pool),
        intake=HttpIntakeClient(http, p.intake_url, p.intake_token),
        lock=PostgresLeaderLock(pool),
        clock=clock,
        source_name=p.source,
        lookback=lookback,
        sweep_interval=timedelta(seconds=p.interval_seconds * p.active_sweep_every_n_cycles),
    )
    return Container(pool, http, poll)
