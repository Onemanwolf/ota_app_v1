import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from insights_testkit import ANCHOR, SCOPES, collector
from pydantic import SecretStr

from opstriage_insights.adapters.evidence_json import serialize
from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.adapters.llm_analyst import ScriptedAnalyst
from opstriage_insights.adapters.stores import MemoryReviewStore
from opstriage_insights.application.collect import BuildDigest, PrepareReview
from opstriage_insights.application.run_review import RunReview
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.entrypoints.api import Runner, api_router
from opstriage_shared.auth import bearer_guard

AUTH = {"Authorization": "Bearer t"}


def _app(now: list[Any]) -> tuple[FastAPI, MemoryReviewStore, Runner]:
    store = MemoryReviewStore()
    prepare = PrepareReview(
        store,
        collector(),
        BuildDigest(KeywordGuard()),
        RetentionPolicy(),
        lambda: ANCHOR,
        lambda: UUID(int=1),
        serialize,
    )
    runner = Runner(RunReview(prepare, ScriptedAnalyst(), store, None, RetentionPolicy(), lambda: ANCHOR))
    app = FastAPI()
    app.include_router(
        api_router(dict(SCOPES), runner, store, bearer_guard([SecretStr("t")]), lambda: now[0])
    )
    return app, store, runner


def test_review_lifecycle_json_markdown_and_expiry() -> None:
    now = [ANCHOR + timedelta(minutes=5)]
    app, _store, runner = _app(now)
    with TestClient(app) as client:
        assert client.post("/v1/reviews", json={"cluster": "planted-disk-trend"}).status_code == 401
        assert client.post("/v1/reviews", json={"cluster": "nope"}, headers=AUTH).status_code == 404
        assert client.post("/v1/reviews", json={"cluster": "x", "extra": 1}, headers=AUTH).status_code == 422
        started = client.post("/v1/reviews", json={"cluster": "planted-disk-trend"}, headers=AUTH)
        assert started.status_code == 202
        rid = started.json()["review_id"]
        client.portal.call(runner.drain)  # type: ignore[union-attr]
        status = client.get(f"/v1/reviews/{rid}", headers=AUTH).json()
        assert status["status"] == "completed" and status["cluster"] == "planted-disk-trend"
        report = client.get(f"/v1/reports/{rid}", headers=AUTH)
        assert report.status_code == 200 and report.headers["cache-control"] == "no-store"
        assert report.json()["review_id"] == rid and report.json()["worst_risk"] == "medium"
        md = client.get(f"/v1/reports/{rid}", headers={**AUTH, "Accept": "text/markdown"})
        assert md.headers["content-type"].startswith("text/markdown") and md.text.startswith(
            "# Kafka health review"
        )
        listing = client.get("/v1/reports?cluster=planted-disk-trend", headers=AUTH).json()["reports"]
        assert [r["review_id"] for r in listing] == [rid] and listing[0]["by_risk"]["medium"] == 1
        now[0] = ANCHOR + timedelta(hours=49)
        gone = client.get(f"/v1/reports/{rid}", headers=AUTH)
        assert gone.status_code == 410 and "expired" in gone.json()["detail"]
        assert client.get("/v1/reports", headers=AUTH).json()["reports"] == []
        assert client.get(f"/v1/reports/{UUID(int=99)}", headers=AUTH).status_code == 404


def test_a_running_review_answers_202() -> None:
    now = [ANCHOR]
    app, store, _ = _app(now)
    asyncio.run(store.start_review(UUID(int=5), "review", "c", None, ANCHOR, ANCHOR + timedelta(hours=28)))
    with TestClient(app) as client:
        r = client.get(f"/v1/reports/{UUID(int=5)}", headers=AUTH)
    assert r.status_code == 202 and r.json()["status"] == "running"


def test_root_cause_analysis_endpoint() -> None:
    from insights_testkit import STORE

    from opstriage_insights.adapters.fixtures import FixtureProblemSource
    from opstriage_insights.application.run_rca import PlanRca

    store = MemoryReviewStore()
    prepare = PrepareReview(
        store,
        collector(),
        BuildDigest(KeywordGuard()),
        RetentionPolicy(),
        lambda: ANCHOR,
        lambda: UUID(int=3),
        serialize,
    )
    runner = Runner(RunReview(prepare, ScriptedAnalyst(), store, None, RetentionPolicy(), lambda: ANCHOR))
    app = FastAPI()
    app.include_router(
        api_router(
            dict(SCOPES),
            runner,
            store,
            bearer_guard([SecretStr("t")]),
            lambda: ANCHOR,
            PlanRca(FixtureProblemSource(STORE), dict(SCOPES), lambda: ANCHOR),
        )
    )
    with TestClient(app) as client:
        assert client.post("/v1/analyses", json={"display_id": "nope"}, headers=AUTH).status_code == 422
        assert client.post("/v1/analyses", json={"display_id": "P-1"}, headers=AUTH).status_code == 404
        started = client.post("/v1/analyses", json={"display_id": "P-26101208"}, headers=AUTH)
        assert started.status_code == 202
        body = started.json()
        assert body["cluster"] == "rca-follower-lag" and body["window"]["end"] == ANCHOR.isoformat()
        client.portal.call(runner.drain)  # type: ignore[union-attr]
        report = client.get(f"/v1/reports/{body['review_id']}", headers=AUTH).json()
    assert report["kind"] == "rca" and report["subject"]["display_id"] == "P-26101208"
    assert report["cluster"] == "rca-follower-lag"
    assert any(e["id"] == "B1" for e in report["evidence"])  # under-min-ISR breach inside the window
