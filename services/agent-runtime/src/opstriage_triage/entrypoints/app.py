from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, status

from opstriage_shared.auth import bearer_guard
from opstriage_triage.bootstrap import Container, build_container
from opstriage_triage.entrypoints.http import build_runs_router
from opstriage_triage.settings import Settings

log = structlog.get_logger()

ContainerFactory = Callable[[Settings], Awaitable[Container]]


def create_app(
    settings: Settings, container_factory: ContainerFactory = build_container, *, start_workers: bool = True
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await container_factory(settings)
        app.state.container = container
        app.include_router(
            build_runs_router(
                container.graph,
                container.deps.runs,
                container.deps.approvals,
                bearer_guard(settings.triage.admin_tokens),
                container.worker.poke,
            )
        )
        stop = asyncio.Event()
        tasks: list[asyncio.Task[None]] = []
        if start_workers:
            tasks = [
                asyncio.create_task(container.worker.run_claim_loop(stop), name=f"claim-{i}")
                for i in range(settings.triage.worker_concurrency)
            ]
            tasks.append(asyncio.create_task(container.worker.run_sweeper(stop), name="sweeper"))
        log.info(
            "agent_runtime_started",
            workers=settings.triage.worker_concurrency,
            decision_provider=settings.triage.decision.provider,
            approval_mode=settings.triage.approval.mode,
        )
        try:
            yield
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await container.close()

    app = FastAPI(title="opstriage agent-runtime", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        container: Container | None = getattr(app.state, "container", None)
        if container is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "starting")
        for pool in (container.pool, container.checkpoint_pool):
            async with pool.connection() as conn:
                await conn.execute("SELECT 1")
        return {"status": "ready"}

    return app
