from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from insights_testkit import ANCHOR, SCOPES, collector

from opstriage_insights.adapters.evidence_json import serialize
from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.adapters.stores import MemoryReviewStore
from opstriage_insights.application.collect import BuildDigest, PrepareReview
from opstriage_insights.application.run_review import RunReview
from opstriage_insights.bootstrap import Container
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.entrypoints.api import Runner
from opstriage_insights.entrypoints.app import create_app
from opstriage_insights.settings import Settings

TOKEN = {"Authorization": "Bearer svc"}


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
    prepare = PrepareReview(
        MemoryReviewStore(),
        collector(),
        BuildDigest(KeywordGuard()),
        RetentionPolicy(),
        lambda: ANCHOR,
        lambda: UUID(int=7),
        serialize,
    )
    store = prepare._store
    run = RunReview(prepare, None, store, None, RetentionPolicy(), lambda: ANCHOR)
    return Container(
        pool=pool, source="fake", clusters=dict(SCOPES), prepare=prepare, store=store, runner=Runner(run)
    )


def _settings(app_env: str = "local") -> Settings:
    return Settings.model_validate(
        {
            "db": {"dsn": "postgresql://unused"},
            "app_env": app_env,
            "insights": {"service_tokens": ["svc"], "web": {"session_secret": "s" * 32}},
        }
    )


def test_health_and_readiness() -> None:
    with TestClient(create_app(_settings(), _container)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ready", "source": "fake", "clusters": 16}
    assert _Pool.closed


def test_dev_digest_endpoint_needs_a_token_and_a_known_cluster() -> None:
    with TestClient(create_app(_settings(), _container)) as client:
        assert client.post("/v1/dev/digest/planted-disk-trend").status_code == 401
        assert client.post("/v1/dev/digest/nope", headers=TOKEN).status_code == 404
        resp = client.post("/v1/dev/digest/planted-disk-trend", headers=TOKEN)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/plain")
    assert resp.text.startswith("CLUSTER planted-disk-trend")
    assert resp.headers["x-review-id"] == str(UUID(int=7)) and int(resp.headers["x-digest-tokens"]) > 0


def test_dev_digest_endpoint_is_not_mounted_outside_local() -> None:
    with TestClient(create_app(_settings("dev"), _container)) as client:
        assert client.post("/v1/dev/digest/planted-disk-trend", headers=TOKEN).status_code == 404
