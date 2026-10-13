from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, status

from opstriage_insights.bootstrap import Container, build_container
from opstriage_insights.settings import Settings

log = structlog.get_logger()

ContainerFactory = Callable[[Settings], Awaitable[Container]]


def create_app(settings: Settings, container_factory: ContainerFactory = build_container) -> FastAPI:
    """API and web routes are mounted in the lifespan once the composition root exists (5.5, 5.7)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await container_factory(settings)
        app.state.container = container
        log.info("health_review_started", source=container.source, clusters=len(container.clusters))
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(title="opstriage health-review", lifespan=lifespan)

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
