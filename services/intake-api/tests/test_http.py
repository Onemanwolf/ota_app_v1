from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from opstriage_intake.adapters.fixtures import DirectoryFixtureLoader
from opstriage_intake.adapters.ids import UuidGenerator
from opstriage_intake.application.get_alert import GetAlert
from opstriage_intake.application.receive_problem_event import ReceiveProblemEvent
from opstriage_intake.bootstrap import Container
from opstriage_intake.entrypoints.app import create_app
from opstriage_intake.settings import Settings

WEBHOOK = {"Authorization": "Bearer hook"}
SERVICE = {"Authorization": "Bearer svc"}


class _NoPool:
    async def close(self) -> None:
        return None


def _client(inbox: Any, fixtures_dir: Path, *, dev: bool = True, max_bytes: int = 262_144) -> TestClient:
    settings = Settings.model_validate(
        {
            "db": {"dsn": "postgresql://unused"},
            "app_env": "local" if dev else "dev",
            "intake": {
                "webhook_tokens": ["hook"],
                "service_tokens": ["svc"],
                "enable_dev_endpoints": True,
                "fixtures_dir": str(fixtures_dir),
                "max_payload_bytes": max_bytes,
            },
        }
    )

    async def factory(s: Settings) -> Container:
        return Container(
            pool=_NoPool(),  # type: ignore[arg-type]
            receive=ReceiveProblemEvent(inbox, UuidGenerator(), max_bytes),
            get_alert=GetAlert(inbox),
            fixtures=DirectoryFixtureLoader(fixtures_dir) if s.dev_endpoints_enabled else None,
        )

    return TestClient(create_app(settings, factory))


@pytest.fixture
def client(inbox: Any, fixtures_dir: Path) -> Iterator[TestClient]:
    with _client(inbox, fixtures_dir) as c:
        yield c


def _body(fixtures_dir: Path, rel: str) -> bytes:
    return (fixtures_dir / rel).read_bytes()


def test_workflow_webhook_accepts_then_deduplicates(
    client: TestClient, inbox: Any, fixtures_dir: Path
) -> None:
    body = _body(fixtures_dir, "workflow/container_down_active.json")
    first = client.post("/v1/webhooks/dynatrace/workflow", content=body, headers=WEBHOOK)
    assert first.status_code == 202 and first.json()["duplicate"] is False
    second = client.post("/v1/webhooks/dynatrace/workflow", content=body, headers=WEBHOOK)
    assert second.status_code == 200 and second.json() == {"alert_id": None, "duplicate": True}
    assert inbox.notified == 1
    alert = client.get(f"/v1/alerts/{first.json()['alert_id']}", headers=SERVICE)
    assert alert.status_code == 200 and alert.json()["display_id"] == "P-2610121"


def test_closed_event_is_a_separate_alert(client: TestClient, fixtures_dir: Path) -> None:
    for rel in ("workflow/container_down_active.json", "workflow/container_down_closed.json"):
        assert (
            client.post(
                "/v1/webhooks/dynatrace/workflow", content=_body(fixtures_dir, rel), headers=WEBHOOK
            ).status_code
            == 202
        )


def test_classic_webhook(client: TestClient, fixtures_dir: Path) -> None:
    r = client.post(
        "/v1/webhooks/dynatrace/classic",
        content=_body(fixtures_dir, "classic/container_down_open.json"),
        headers=WEBHOOK,
    )
    assert r.status_code == 202


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, SERVICE])
def test_webhook_rejects_bad_tokens(client: TestClient, fixtures_dir: Path, headers: dict[str, str]) -> None:
    body = _body(fixtures_dir, "workflow/container_down_active.json")
    assert client.post("/v1/webhooks/dynatrace/workflow", content=body, headers=headers).status_code == 401


def test_canonical_endpoint_needs_service_token(client: TestClient) -> None:
    assert client.post("/v1/alerts", json={}, headers=WEBHOOK).status_code == 401


@pytest.mark.parametrize("content", [b"{not json", b"[1, 2]"])
def test_non_object_bodies_are_422(client: TestClient, content: bytes) -> None:
    assert client.post("/v1/webhooks/dynatrace/workflow", content=content, headers=WEBHOOK).status_code == 422


def test_unmappable_payload_is_422(client: TestClient, fixtures_dir: Path) -> None:
    r = client.post(
        "/v1/webhooks/dynatrace/workflow",
        content=_body(fixtures_dir, "workflow/malformed.json"),
        headers=WEBHOOK,
    )
    assert r.status_code == 422


def test_oversized_payload_is_413(inbox: Any, fixtures_dir: Path) -> None:
    with _client(inbox, fixtures_dir, max_bytes=2_048) as c:
        big = b'{"pad": "' + b"x" * 4_096 + b'"}'
        assert c.post("/v1/webhooks/dynatrace/workflow", content=big, headers=WEBHOOK).status_code == 413


def test_dev_simulator_uses_fixture_and_rejects_traversal(client: TestClient) -> None:
    assert client.post("/v1/dev/simulate/workflow/container_down_active", headers=WEBHOOK).status_code == 202
    assert client.post("/v1/dev/simulate/workflow/nope", headers=WEBHOOK).status_code == 404
    assert client.post("/v1/dev/simulate/workflow/..%2F..%2Fetc", headers=WEBHOOK).status_code == 404
    assert client.post("/v1/dev/simulate/grail/problems_batch_01", headers=WEBHOOK).status_code == 404


def test_dev_simulator_absent_outside_local(inbox: Any, fixtures_dir: Path) -> None:
    with _client(inbox, fixtures_dir, dev=False) as c:
        assert c.post("/v1/dev/simulate/workflow/container_down_active", headers=WEBHOOK).status_code == 404


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
