from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, status

from opstriage_intake.bootstrap import Container, build_container
from opstriage_intake.entrypoints.http import build_router
from opstriage_intake.entrypoints.payload_limit import PayloadLimitMiddleware
from opstriage_intake.settings import Settings
from opstriage_shared.auth import bearer_guard

log = structlog.get_logger()

ContainerFactory = Callable[[Settings], Awaitable[Container]]


def create_app(settings: Settings, container_factory: ContainerFactory = build_container) -> FastAPI:
    """Routes are mounted in the lifespan, once the composition root is built."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await container_factory(settings)
        app.state.container = container
        app.include_router(
            build_router(
                container.receive,
                container.get_alert,
                webhook_guard=bearer_guard(settings.intake.webhook_tokens),
                service_guard=bearer_guard(settings.intake.service_tokens),
                fixtures=container.fixtures,
            )
        )
        log.info("intake_started", dev_endpoints=container.fixtures is not None)
        try:
            yield
        finally:
            await container.close()

    app = FastAPI(title="opstriage intake-api", lifespan=lifespan)
    app.add_middleware(PayloadLimitMiddleware, max_bytes=settings.intake.max_payload_bytes)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        container: Container | None = getattr(app.state, "container", None)
        if container is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "starting")
        async with container.pool.connection() as conn:
            await conn.execute("SELECT 1")
        return {"status": "ready"}

    return app
