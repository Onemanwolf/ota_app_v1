from __future__ import annotations

import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from opstriage_poller.domain.cursor import Cursor
from opstriage_shared.db import Pool

LOCK_KEY = zlib.crc32(b"opstriage-poller")


class PostgresCursorStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def get(self, source: str) -> Cursor | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT source, high_water_mark, last_sweep_at FROM poller.cursors WHERE source = %s",
                (source,),
            )
            row = await cur.fetchone()
        return Cursor(**row) if row else None

    async def set(self, source: str, *, high_water_mark: datetime, last_sweep_at: datetime | None) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO poller.cursors (source, high_water_mark, last_sweep_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (source) DO UPDATE SET "
                "high_water_mark = GREATEST(poller.cursors.high_water_mark, EXCLUDED.high_water_mark), "
                "last_sweep_at = EXCLUDED.last_sweep_at, updated_at = now()",
                (source, high_water_mark, last_sweep_at),
            )


class PostgresLeaderLock:
    """Session-level advisory lock held on one pooled connection for the whole cycle."""

    def __init__(self, pool: Pool, key: int = LOCK_KEY) -> None:
        self._pool, self._key = pool, key

    @asynccontextmanager
    async def try_acquire(self) -> AsyncIterator[bool]:
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (self._key,))
            row = await cur.fetchone()
            got = bool(row and row["got"])
            try:
                yield got
            finally:
                if got:
                    await conn.execute("SELECT pg_advisory_unlock(%s)", (self._key,))
