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
    assert ready.json() == {"status": "ready", "source": "fake", "clusters": 16}  # fixtures/insights mounted
