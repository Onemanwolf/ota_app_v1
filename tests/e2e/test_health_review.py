"""Compose tests for health-review (OTA-APP-002). Run: uv run pytest -m compose"""

from __future__ import annotations

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
