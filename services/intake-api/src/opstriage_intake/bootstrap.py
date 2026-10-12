"""Composition root: the only module that knows concrete adapter classes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opstriage_intake.adapters.fixtures import DirectoryFixtureLoader
from opstriage_intake.adapters.ids import UuidGenerator
from opstriage_intake.adapters.inbox_pg import PostgresInboxRepository, grant_consumer
from opstriage_intake.application.get_alert import GetAlert
from opstriage_intake.application.receive_problem_event import ReceiveProblemEvent
from opstriage_intake.ports.fixtures import FixtureLoader
from opstriage_intake.settings import Settings
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


@dataclass(slots=True)
class Container:
    pool: Pool
    receive: ReceiveProblemEvent
    get_alert: GetAlert
    fixtures: FixtureLoader | None

    async def close(self) -> None:
        await self.pool.close()


async def build_container(settings: Settings, migrations_dir: Path = MIGRATIONS) -> Container:
    pool = await open_pool(settings.db)
    await migrate(pool, "intake", migrations_dir)
    if settings.intake.inbox_consumer_role:
        await grant_consumer(pool, settings.intake.inbox_consumer_role)
    inbox = PostgresInboxRepository(pool)
    return Container(
        pool=pool,
        receive=ReceiveProblemEvent(inbox, UuidGenerator(), settings.intake.max_payload_bytes),
        get_alert=GetAlert(inbox),
        fixtures=DirectoryFixtureLoader(settings.intake.fixtures_dir)
        if settings.dev_endpoints_enabled
        else None,
    )
