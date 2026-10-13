"""Composition root: the only module that knows concrete adapter classes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opstriage_insights.adapters.fixtures import (
    FixtureLogSource,
    FixtureMetricSource,
    FixtureProblemSource,
    FixtureStore,
)
from opstriage_insights.ports.sources import LogSource, MetricSource, ProblemSource
from opstriage_insights.settings import Settings
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


@dataclass(slots=True)
class Container:
    pool: Pool
    source: str  # "fake" | "grail"
    logs: LogSource
    metrics: MetricSource
    problems: ProblemSource
    clusters: list[str]  # clusters the fake source can serve; the Grail source reads config (5.4)

    async def close(self) -> None:
        await self.pool.close()


async def build_container(settings: Settings, migrations_dir: Path = MIGRATIONS) -> Container:
    pool = await open_pool(settings.db)
    await migrate(pool, "insights", migrations_dir)
    if settings.insights.source != "fake":
        raise NotImplementedError("INSIGHTS__SOURCE=grail is wired in Module 5.4")
    store = FixtureStore(settings.insights.fixtures_dir)
    return Container(
        pool=pool,
        source="fake",
        logs=FixtureLogSource(store),
        metrics=FixtureMetricSource(store),
        problems=FixtureProblemSource(store),
        clusters=store.clusters(),
    )
