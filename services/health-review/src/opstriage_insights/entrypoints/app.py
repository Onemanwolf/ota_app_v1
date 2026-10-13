from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, status

from opstriage_insights.bootstrap import Container, build_container
from opstriage_insights.entrypoints.api import api_router
from opstriage_insights.entrypoints.web import WebConfig, add_security_headers, web_router
from opstriage_insights.entrypoints.web_session import SessionManager
from opstriage_insights.settings import Settings
from opstriage_shared.auth import bearer_guard

log = structlog.get_logger()

ContainerFactory = Callable[[Settings], Awaitable[Container]]


def dev_router(container: Container, guard: Callable[..., None]) -> APIRouter:
    """Local-only preview of the digest the analyst sees (no model call, no report)."""
    router = APIRouter(prefix="/v1/dev", dependencies=[Depends(guard)])

    @router.post("/digest/{cluster}")
    async def digest(cluster: str) -> Response:
        scope = container.clusters.get(cluster)
        if scope is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown cluster")
        prepared = await container.prepare(scope)
        d = prepared.digest
        log.info(
            "digest_built",
            cluster=cluster,
            tokens=d.tokens,
            metrics=len(d.metrics),
            logs=len(d.logs),
            breaches=len(d.breaches),
            guard_withheld=d.guard_withheld,
            guard_available=d.guard_available,
        )
        return Response(
            d.render(),
            media_type="text/plain; charset=utf-8",
            headers={"X-Review-Id": str(prepared.review_id), "X-Digest-Tokens": str(d.tokens)},
        )

    @router.post("/expire/{review_id}")
    async def expire(review_id: UUID) -> dict[str, Any]:
        """Make one review expire now and run the purge (retention tests without waiting 48 hours)."""
        now = datetime.now(UTC)
        if not await container.store.expire_review(review_id, now):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown review")
        counts = await container.purge(now)
        return {"review_id": str(review_id), **asdict(counts)}

    return router


def create_app(settings: Settings, container_factory: ContainerFactory = build_container) -> FastAPI:
    """API and web routes are mounted in the lifespan once the composition root exists (5.5, 5.7)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await container_factory(settings)
        app.state.container = container
        guard = bearer_guard(settings.insights.service_tokens)
        app.include_router(
            api_router(
                container.clusters, container.runner, container.store, guard, plan_rca=container.plan_rca
            )
        )
        w = settings.insights.web
        sessions = SessionManager(
            w.session_secret.get_secret_value(),
            w.session_ttl,
            proxy_header=w.trusted_proxy_header,
            proxy_addresses=w.trusted_proxy_addresses,
        )
        app.include_router(
            web_router(
                container.clusters,
                container.runner,
                container.store,
                sessions,
                WebConfig(tuple(settings.insights.service_tokens), w.allow_download),
                container.plan_rca,
            )
        )
        if settings.app_env == "local":
            app.include_router(dev_router(container, guard))
        if container.scheduler is not None:
            container.scheduler.start()
        log.info("health_review_started", source=container.source, clusters=len(container.clusters))
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(title="opstriage health-review", lifespan=lifespan)

    add_security_headers(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        container: Container | None = getattr(app.state, "container", None)
        if container is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "starting")
        async with container.pool.connection() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ready", "source": container.source, "clusters": len(container.clusters)}

    return app
