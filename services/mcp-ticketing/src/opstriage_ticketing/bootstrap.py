"""Composition root for mcp-ticketing."""

from __future__ import annotations

from pathlib import Path

from opstriage_shared.db import Pool, migrate, open_pool
from opstriage_ticketing.adapters.stores import PostgresTicketStore
from opstriage_ticketing.application.tickets import TicketService
from opstriage_ticketing.settings import Settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


class Container:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: Pool | None = None
        self._tickets: TicketService | None = None

    @property
    def tickets(self) -> TicketService:
        if self._tickets is None:
            raise RuntimeError("container not started")
        return self._tickets

    async def start(self) -> None:
        self._pool = await open_pool(self._settings.db)
        await migrate(self._pool, "ticketing", MIGRATIONS)
        self._tickets = TicketService(
            PostgresTicketStore(self._pool), self._settings.ticketing.public_base_url
        )

    async def stop(self) -> None:
        if self._pool is not None:
            await self._pool.close()
