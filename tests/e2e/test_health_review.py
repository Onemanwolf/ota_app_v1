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


async def test_root_cause_analysis_end_to_end() -> None:
    """POST /v1/analyses for a fixture problem -> RCA report with ranked hypotheses (live analyst)."""
    import asyncio

    headers = {"Authorization": "Bearer dev-insights-token"}
    async with httpx2.AsyncClient(timeout=30) as client:
        started = await client.post(
            f"{HEALTH_REVIEW}/v1/analyses", json={"display_id": "P-26101208"}, headers=headers
        )
        assert started.status_code == 202, started.text
        body = started.json()
        assert body["cluster"] == "rca-follower-lag"
        async with asyncio.timeout(300):
            while True:
                status = (
                    await client.get(f"{HEALTH_REVIEW}/v1/reviews/{body['review_id']}", headers=headers)
                ).json()
                if status["status"] != "running":
                    break
                await asyncio.sleep(3)
        assert status["status"] == "completed" and status["kind"] == "rca"
        report = (await client.get(f"{HEALTH_REVIEW}/v1/reports/{body['review_id']}", headers=headers)).json()
    assert report["subject"]["display_id"] == "P-26101208"
    if report["analyst_available"]:  # with a model: ranked hypotheses first
        hyps = [f for f in report["findings"] if f["kind"] == "root_cause_hypothesis"]
        assert hyps and hyps[0]["rank"] == 1 and report["findings"][0] is not None
    assert any(e["type"] == "breach" for e in report["evidence"])


async def test_web_view_sign_in_list_and_report() -> None:
    """Browser flow against the running stack: sign in with a token, list reports, open one, check headers."""
    import re

    headers = {"Authorization": "Bearer dev-insights-token"}
    async with httpx2.AsyncClient(timeout=30, follow_redirects=False) as client:
        redirect = await client.get(f"{HEALTH_REVIEW}/ui/reports")
        assert redirect.status_code == 303 and redirect.headers["location"].startswith("/ui/login")
        login = await client.post(
            f"{HEALTH_REVIEW}/ui/login", data={"token": "dev-insights-token", "next": "/ui/reports"}
        )
        assert login.status_code == 303 and "httponly" in login.headers["set-cookie"].lower()
        listing = await client.get(f"{HEALTH_REVIEW}/ui/reports")
        assert listing.status_code == 200 and "Content-Security-Policy" in listing.headers
        reports = (await client.get(f"{HEALTH_REVIEW}/v1/reports", headers=headers)).json()["reports"]
        done = [r for r in reports if r["status"] == "completed"]
        assert done, "run the end-to-end review test first"
        page = await client.get(f"{HEALTH_REVIEW}/ui/reports/{done[0]['review_id']}")
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store"
    assert set(re.findall(r'href="#(ev-[a-z0-9]+)"', page.text)) <= set(
        re.findall(r'id="(ev-[a-z0-9]+)"', page.text)
    )
    assert "<script" not in page.text


async def test_retention_purge_deletes_content_and_answers_410() -> None:
    """Expire one review through the local dev endpoint: evidence and report go, links answer 410 (5.8)."""
    headers = {"Authorization": "Bearer dev-insights-token"}
    async with httpx2.AsyncClient(timeout=60, follow_redirects=False) as client:
        digest = await client.post(f"{HEALTH_REVIEW}/v1/dev/digest/planted-disk-trend", headers=headers)
        rid = digest.headers["x-review-id"]
        counts = (await client.post(f"{HEALTH_REVIEW}/v1/dev/expire/{rid}", headers=headers)).json()
        assert counts["evidence_deleted"] >= 3  # logs, metrics, problems bundles of this review
        gone = await client.get(f"{HEALTH_REVIEW}/v1/reports/{rid}", headers=headers)
        assert gone.status_code == 410
        await client.post(
            f"{HEALTH_REVIEW}/ui/login", data={"token": "dev-insights-token", "next": "/ui/reports"}
        )
        page = await client.get(f"{HEALTH_REVIEW}/ui/reports/{rid}")
        listed = (await client.get(f"{HEALTH_REVIEW}/v1/reports", headers=headers)).json()["reports"]
    assert page.status_code == 410 and "has expired" in page.text
    assert rid not in {r["review_id"] for r in listed}
