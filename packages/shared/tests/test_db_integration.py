from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from opstriage_shared.db import Pool, migrate, open_pool
from opstriage_shared.settings import DatabaseSettings

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg() -> Iterator[str]:
    with PostgresContainer("postgres:17", driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture
async def pool(pg: str) -> AsyncIterator[Pool]:
    p = await open_pool(DatabaseSettings(dsn=SecretStr(pg), pool_max_size=4))
    yield p
    await p.close()


async def test_migrations_apply_once_in_order(pool: Pool, tmp_path: Path) -> None:
    (tmp_path / "001_create.sql").write_text("CREATE TABLE IF NOT EXISTS demo.items (id int PRIMARY KEY);")
    (tmp_path / "002_seed.sql").write_text("INSERT INTO demo.items VALUES (1);")
    (tmp_path / "notes.txt").write_text("ignored")
    assert await migrate(pool, "demo", tmp_path) == ["001_create.sql", "002_seed.sql"]
    assert await migrate(pool, "demo", tmp_path) == []  # idempotent: 002 would fail on a second insert
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM demo.items")
        assert (await cur.fetchone()) == {"n": 1}


async def test_failed_migration_is_not_recorded(pool: Pool, tmp_path: Path) -> None:
    (tmp_path / "001_bad.sql").write_text("CREATE TABLE broken.t (id int); SELECT 1/0;")
    with pytest.raises(Exception, match="division by zero"):
        await migrate(pool, "broken", tmp_path)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM broken.schema_migrations")
        assert (await cur.fetchone()) == {"n": 0}


async def test_rejects_unsafe_schema_names(pool: Pool, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid schema"):
        await migrate(pool, "x; DROP SCHEMA public", tmp_path)
