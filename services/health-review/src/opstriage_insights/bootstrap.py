"""Composition root: the only module that knows concrete adapter classes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opstriage_insights.settings import Settings
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


@dataclass(slots=True)
class Container:
    pool: Pool
    source: str  # "fake" | "grail": which LogSource/MetricSource adapters are wired (5.4)

    async def close(self) -> None:
        await self.pool.close()


async def build_container(settings: Settings, migrations_dir: Path = MIGRATIONS) -> Container:
    pool = await open_pool(settings.db)
    await migrate(pool, "insights", migrations_dir)
    return Container(pool=pool, source=settings.insights.source)
