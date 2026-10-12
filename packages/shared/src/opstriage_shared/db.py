from __future__ import annotations

import asyncio
import re
import zlib
from pathlib import Path
from typing import Any

import structlog
from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from opstriage_shared.settings import DatabaseSettings

log = structlog.get_logger()

_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_MIGRATION_FILE = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


async def open_pool(settings: DatabaseSettings, *, search_path: str | None = None) -> Pool:
    """Open a pool; callers close it in their lifespan. Created closed, then opened (non-deprecated)."""
    kwargs: dict[str, Any] = {"autocommit": True, "row_factory": dict_row, "prepare_threshold": None}
    if search_path:
        kwargs["options"] = f"-c search_path={search_path}"

    connection_class: Any = None
    if settings.auth_mode == "entra":
        # azure-postgresql-auth fetches a fresh Entra token for each new physical connection.
        # Installed only with the `entra` extra; local stacks never take this path.
        from azure.identity.aio import ManagedIdentityCredential
        from azure_postgresql_auth.psycopg3 import AsyncEntraConnection

        connection_class = AsyncEntraConnection
        kwargs["credential"] = ManagedIdentityCredential(client_id=settings.entra_client_id)

    pool: Pool = AsyncConnectionPool(
        conninfo=settings.dsn.get_secret_value(),
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        max_lifetime=settings.pool_max_lifetime_seconds,
        kwargs=kwargs,
        open=False,
        **({"connection_class": connection_class} if connection_class else {}),
    )
    await pool.open(wait=True, timeout=30)
    return pool


async def migrate(pool: Pool, schema: str, migrations_dir: Path) -> list[str]:
    """Apply `NNN_description.sql` files in order, once each, under a PostgreSQL advisory lock.

    Applied files are recorded in `<schema>.schema_migrations`. Returns the names applied by this call.
    """
    if not _SCHEMA_NAME.match(schema):
        raise ValueError(f"invalid schema name: {schema!r}")
    files = await asyncio.to_thread(_read_migrations, migrations_dir)
    lock_key = zlib.crc32(f"opstriage-migrate:{schema}".encode())
    applied: list[str] = []
    async with pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
        try:
            ident = sql.Identifier(schema)
            await conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(ident))
            await conn.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {}.schema_migrations "
                    "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                ).format(ident)
            )
            cur = await conn.execute(sql.SQL("SELECT name FROM {}.schema_migrations").format(ident))
            done = {row["name"] for row in await cur.fetchall()}
            for name, text in files:
                if name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(text.encode(), prepare=False)
                    await conn.execute(
                        sql.SQL("INSERT INTO {}.schema_migrations (name) VALUES (%s)").format(ident),
                        (name,),
                    )
                applied.append(name)
                log.info("migration_applied", schema=schema, migration=name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
    return applied


def _read_migrations(migrations_dir: Path) -> list[tuple[str, str]]:
    paths = sorted(p for p in migrations_dir.glob("*.sql") if _MIGRATION_FILE.match(p.name))
    return [(p.name, p.read_text(encoding="utf-8")) for p in paths]
