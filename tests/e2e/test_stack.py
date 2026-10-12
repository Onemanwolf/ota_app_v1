"""End-to-end tests against the running compose stack (spec 12.2.8). Run: uv run pytest -m compose

Each test sends a fixture with a fresh event_id/display_id, so reruns never collide with deduplication.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from pathlib import Path
from typing import Any

import httpx2
import pytest

pytestmark = pytest.mark.compose

ROOT = Path(__file__).resolve().parents[2]
INTAKE = os.environ.get("E2E_INTAKE_URL", "http://localhost:8000")
AGENT = os.environ.get("E2E_AGENT_URL", "http://localhost:8010")
SINK = os.environ.get("E2E_SINK_URL", "http://localhost:8091")
MAILPIT = os.environ.get("E2E_MAILPIT_URL", "http://localhost:8025")
WEBHOOK = {"Authorization": "Bearer dev-webhook-token"}
SERVICE = {"Authorization": "Bearer dev-poller-token"}
ADMIN = {"Authorization": "Bearer dev-admin-token"}


def fixture(name: str, *, event_id: str | None = None, display_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((ROOT / "fixtures/dynatrace/workflow" / f"{name}.json").read_text())
    body["event_id"] = event_id or f"-{random.randrange(10**18, 10**19)}_1760300000000V2"  # noqa: S311
    body["display_id"] = display_id or f"P-E2E{random.randrange(10**6, 10**7)}"  # noqa: S311
    return body


async def post(client: httpx2.AsyncClient, body: dict[str, Any]) -> httpx2.Response:
    return await client.post(f"{INTAKE}/v1/webhooks/dynatrace/workflow", json=body, headers=WEBHOOK)


async def wait_done(client: httpx2.AsyncClient, alert_id: str, within: float = 15) -> dict[str, Any]:
    async with asyncio.timeout(within):
        while True:
            alert = (await client.get(f"{INTAKE}/v1/alerts/{alert_id}", headers=SERVICE)).json()
            if alert["state"] in {"done", "failed", "awaiting_approval"}:
                return alert  # type: ignore[no-any-return]
            await asyncio.sleep(0.3)


async def run_of(client: httpx2.AsyncClient, alert_id: str) -> dict[str, Any]:
    return (await client.get(f"{AGENT}/v1/runs/{alert_id}", headers=ADMIN)).json()  # type: ignore[no-any-return]


@pytest.fixture
async def client() -> Any:
    async with httpx2.AsyncClient(timeout=10) as c:
        yield c


async def test_container_down_flows_end_to_end_within_10_seconds(client: httpx2.AsyncClient) -> None:
    body = fixture("container_down_active")
    first = await post(client, body)
    assert first.status_code == 202
    alert_id = first.json()["alert_id"]
    duplicate = await post(client, body)
    assert duplicate.status_code == 200 and duplicate.json() == {"alert_id": None, "duplicate": True}

    alert = await wait_done(client, alert_id, within=10)  # success criterion 1.3: within 10 seconds
    assert alert["state"] == "done"
    run = await run_of(client, alert_id)
    assert run["state"] == "completed" and run["decision"]["action"] == "restart_container"
    outcomes = {o["step"]: o for o in run["outcomes"]}
    assert outcomes["notify"]["ok"]
    remediate = outcomes["remediate"]
    # Default stack: dry run. Within 15 min of a REAL restart (Postman folder 9) the target's cooldown, which the
    # server checks before the dry-run step (spec 12.5.2), rejects even dry runs.
    assert remediate["status"] == "dry_run" or (
        remediate["status"] == "rejected" and remediate["reason"].startswith("cooldown")
    )
    assert outcomes["remediate"]["approved"] is True

    cards = (await client.get(f"{SINK}/requests")).json()
    titles = [c["body"]["attachments"][0]["content"]["body"][0]["text"] for c in cards]
    assert any(body["display_id"] in t for t in titles)
    mails = (await client.get(f"{MAILPIT}/api/v1/search", params={"query": body["display_id"]})).json()
    assert mails["messages_count"] >= 1


async def test_closed_event_resolves(client: httpx2.AsyncClient) -> None:
    active = fixture("container_down_active")
    opened = (await post(client, active)).json()["alert_id"]
    await wait_done(client, opened)
    closed = fixture("container_down_closed", event_id=active["event_id"], display_id=active["display_id"])
    resp = await post(client, closed)
    assert resp.status_code == 202
    await wait_done(client, resp.json()["alert_id"])
    run = await run_of(client, resp.json()["alert_id"])
    [outcome] = run["outcomes"]
    assert outcome["step"] == "resolve" and outcome["ticket_id"]


@pytest.mark.parametrize(
    ("name", "expected_plan"),
    [
        ("high_error_rate_active", ["notify"]),
        ("maintenance_window_active", []),
        ("ambiguous_target_active", ["notify", "escalate"]),
        ("unknown_entity_active", ["notify"]),
    ],
)
async def test_routing_scenarios(client: httpx2.AsyncClient, name: str, expected_plan: list[str]) -> None:
    resp = await post(client, fixture(name))
    alert_id = resp.json()["alert_id"]
    await wait_done(client, alert_id)
    assert (await run_of(client, alert_id))["plan"] == expected_plan


async def test_security_controls(client: httpx2.AsyncClient) -> None:
    body = fixture("container_down_active")
    url = f"{INTAKE}/v1/webhooks/dynatrace/workflow"
    assert (await client.post(url, json=body)).status_code == 401
    assert (await client.post(url, json=body, headers={"Authorization": "Bearer wrong"})).status_code == 401
    assert (await client.post(url, content=b"{nope", headers=WEBHOOK)).status_code == 422
    big = json.dumps({"pad": "x" * 300_000}).encode()
    assert (await client.post(url, content=big, headers=WEBHOOK)).status_code == 413
    assert (await client.get(f"{AGENT}/v1/runs/whatever")).status_code == 401


async def test_poller_fed_the_grail_fixture_batch_through_the_pipeline(client: httpx2.AsyncClient) -> None:
    """Module 2 DoD: the poller (POLLER__SOURCE=fake) feeds the fixture batch end to end."""
    for display_id, expected_plan in (
        ("P-2610130", ["notify", "remediate", "ticket"]),
        ("P-2610131", ["notify"]),
    ):
        async with asyncio.timeout(90):  # the poller cycles every POLLER__INTERVAL_SECONDS
            while True:
                runs = [r for r in await _runs_for(display_id) if r]
                if runs:
                    break
                await asyncio.sleep(2)
        assert runs[0]["plan"] == expected_plan


async def _runs_for(display_id: str) -> list[dict[str, Any]]:
    """Read the poller-created alert and its run through the public APIs."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(
        os.environ.get("E2E_DB_DSN", "postgresql://opstriage:opstriage@localhost:5432/opstriage")
    ) as conn:
        cur = await conn.execute(
            "SELECT alert_id FROM intake.alert_inbox "
            "WHERE display_id = %s AND source = 'dynatrace.poller.grail' AND state = 'done'",
            (display_id,),
        )
        ids = [str(row[0]) for row in await cur.fetchall()]
    async with httpx2.AsyncClient(timeout=10) as c:
        return [await run_of(c, alert_id) for alert_id in ids]
