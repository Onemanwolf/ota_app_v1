from typing import Any

from fastapi.testclient import TestClient

from opstriage_insights.bootstrap import Container
from opstriage_insights.entrypoints.app import create_app
from opstriage_insights.settings import Settings


class _Conn:
    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str) -> None:
        assert sql == "SELECT 1"


class _Pool:
    closed = False

    def connection(self) -> _Conn:
        return _Conn()

    async def close(self) -> None:
        _Pool.closed = True


async def _container(settings: Settings) -> Container:
    pool: Any = _Pool()
    unused: Any = None
    return Container(
        pool=pool,
        source=settings.insights.source,
        logs=unused,
        metrics=unused,
        problems=unused,
        clusters=["c1", "c2"],
    )


def test_health_and_readiness() -> None:
    settings = Settings.model_validate({"db": {"dsn": "postgresql://unused"}})
    with TestClient(create_app(settings, _container)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready", "source": "fake", "clusters": 2}
    assert _Pool.closed
