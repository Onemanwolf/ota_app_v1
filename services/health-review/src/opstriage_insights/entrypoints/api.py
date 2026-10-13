"""JSON API (OTA-APP-002 section 7). Bearer tokens from INSIGHTS__SERVICE_TOKENS; no payloads logged."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from opstriage_insights.application.run_review import RunReview
from opstriage_insights.domain.model import ClusterScope
from opstriage_insights.ports.store import ReviewRow, ReviewStore

log = structlog.get_logger()


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cluster: str = Field(min_length=1, max_length=80)


def _row(r: ReviewRow) -> dict[str, Any]:
    return {
        "review_id": str(r.review_id),
        "kind": r.kind,
        "cluster": r.cluster,
        "status": r.status,
        "started_at": r.started_at.isoformat(),
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        "expires_at": r.expires_at.isoformat(),
        "worst_risk": r.worst_risk,
        "by_risk": r.by_risk,
        "error": r.error,
    }


class Runner:
    """Runs reviews in the background and keeps a reference to each task until it finishes."""

    def __init__(self, run: RunReview) -> None:
        self._run, self._tasks = run, set[asyncio.Task[Any]]()

    def start(self, scope: ClusterScope, subject: dict[str, Any] | None = None) -> UUID:
        review_id = uuid.uuid4()
        task = asyncio.create_task(self._guarded(scope, subject, review_id), name=f"review-{review_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return review_id

    async def _guarded(self, scope: ClusterScope, subject: dict[str, Any] | None, review_id: UUID) -> None:
        try:
            outcome = await self._run(scope, subject, review_id)
            log.info(
                "review_completed",
                review_id=outcome.review_id,
                cluster=scope.name,
                findings=len(outcome.report["findings"]),
                worst_risk=outcome.report["worst_risk"],
                notified=outcome.notified,
            )
        except Exception as exc:  # recorded on the review row by the use case
            log.warning(
                "review_failed", review_id=str(review_id), cluster=scope.name, error=type(exc).__name__
            )

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


def api_router(
    clusters: dict[str, ClusterScope],
    runner: Runner,
    store: ReviewStore,
    guard: Callable[..., None],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    router = APIRouter(prefix="/v1", dependencies=[Depends(guard)])

    @router.post("/reviews", status_code=status.HTTP_202_ACCEPTED)
    async def start_review(body: ReviewRequest) -> dict[str, Any]:
        scope = clusters.get(body.cluster)
        if scope is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown cluster")
        review_id = runner.start(scope)
        return {
            "review_id": str(review_id),
            "status": "running",
            "links": {"status": f"/v1/reviews/{review_id}", "report": f"/v1/reports/{review_id}"},
        }

    @router.get("/reviews/{review_id}")
    async def review(review_id: UUID) -> dict[str, Any]:
        row = await store.get_review(review_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown review")
        return _row(row)

    @router.get("/reports")
    async def reports(cluster: str | None = None) -> dict[str, Any]:
        return {"reports": [_row(r) for r in await store.list_reports(clock(), cluster)]}

    @router.get("/reports/{review_id}")
    async def report(review_id: UUID, request: Request) -> Response:
        stored = await store.get_report(review_id, clock())
        if stored is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown report")
        if stored.expired:
            raise HTTPException(status.HTTP_410_GONE, f"report expired at {stored.expires_at.isoformat()}")
        if stored.report is None:
            return JSONResponse({"review_id": str(review_id), "status": "running"}, status.HTTP_202_ACCEPTED)
        headers = {"Cache-Control": "no-store"}
        if "text/markdown" in request.headers.get("accept", ""):
            return Response(stored.markdown or "", media_type="text/markdown; charset=utf-8", headers=headers)
        return JSONResponse(stored.report, headers=headers)

    return router
