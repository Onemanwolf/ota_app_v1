"""Compose tests for health-review (OTA-APP-002). Run: uv run pytest -m compose"""

from __future__ import annotations

import json
import os

import httpx2
import pytest

pytestmark = pytest.mark.compose

HEALTH_REVIEW = os.environ.get("E2E_HEALTH_REVIEW_URL", "http://localhost:8120")


async def test_health_review_is_up_migrated_and_serves_the_fixture_clusters() -> None:
    async with httpx2.AsyncClient(timeout=10) as client:
        assert (await client.get(f"{HEALTH_REVIEW}/healthz")).json() == {"status": "ok"}
        ready = await client.get(f"{HEALTH_REVIEW}/readyz")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready" and body["source"] == "fake"
    assert body["clusters"] >= 16  # the fixtures/insights clusters plus any in config/insights.example.yaml


async def test_dev_digest_runs_the_pipeline_and_never_passes_injected_text() -> None:
    headers = {"Authorization": "Bearer dev-insights-token"}
    async with httpx2.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{HEALTH_REVIEW}/v1/dev/digest/planted-injected-log", headers=headers)
    assert resp.status_code == 200
    text = resp.text
    assert text.startswith("CLUSTER planted-injected-log") and "under-replicated | kafka-0" in text
    assert (
        "previous instructions" not in text.lower()
    )  # guard flagged it, or the guard was down (fail closed)
    assert "[withheld: flagged by the injection guard]" in text or "guard was unavailable" in text
    assert int(resp.headers["x-digest-tokens"]) <= 12_000


async def test_review_runs_end_to_end_with_report_and_teams_card() -> None:
    """POST /v1/reviews -> background review (live analyst, or the rules-only fallback if it is unreachable)."""
    import asyncio

    headers = {"Authorization": "Bearer dev-insights-token"}
    sink = os.environ.get("E2E_SINK_URL", "http://localhost:8091")
    async with httpx2.AsyncClient(timeout=30) as client:
        started = await client.post(
            f"{HEALTH_REVIEW}/v1/reviews", json={"cluster": "planted-under-min-isr"}, headers=headers
        )
        assert started.status_code == 202
        rid = started.json()["review_id"]
        async with asyncio.timeout(300):
            while True:
                status = (await client.get(f"{HEALTH_REVIEW}/v1/reviews/{rid}", headers=headers)).json()
                if status["status"] != "running":
                    break
                await asyncio.sleep(3)
        assert status["status"] == "completed", status
        report = (await client.get(f"{HEALTH_REVIEW}/v1/reports/{rid}", headers=headers)).json()
        assert (
            report["worst_risk"] == "critical"
        )  # under-min-ISR is a critical rule finding whatever the model says
        assert any(e["type"] == "breach" and e["rule"] == "under-min-isr" for e in report["evidence"])
        md = await client.get(
            f"{HEALTH_REVIEW}/v1/reports/{rid}", headers={**headers, "Accept": "text/markdown"}
        )
        assert md.text.startswith("# Kafka health review: planted-under-min-isr")
        cards = (await client.get(f"{sink}/requests")).json()
    texts = [json.dumps(c["body"]) for c in cards]
    assert any("Kafka health review: planted-under-min-isr" in t and rid in t for t in texts)
