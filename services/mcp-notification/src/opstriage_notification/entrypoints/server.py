from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from opstriage_contracts.tools import NotificationRequest, NotificationResult
from opstriage_notification.bootstrap import Container
from opstriage_notification.settings import Settings
from opstriage_shared.asgi import BearerTokenMiddleware


def build_server(c: Container) -> MCPServer:
    server = MCPServer(
        "opstriage-notification", instructions="Notify on-call people through Teams and email."
    )

    @server.tool(
        title="Send notification",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True
        ),
    )
    async def send_notification(request: NotificationRequest) -> NotificationResult:
        return await c.send_notification(request)

    return server


def build_app(settings: Settings, container: Container) -> Starlette:
    server = build_server(container)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await container.start()
        try:
            async with server.session_manager.run():
                yield
        finally:
            await container.stop()

    mcp_app = server.streamable_http_app(
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.server.allowed_hosts,
            allowed_origins=[],
        ),
    )

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/healthz", healthz), Mount("/", app=mcp_app)], lifespan=lifespan)
    app.add_middleware(BearerTokenMiddleware, tokens=settings.auth.inbound_tokens, exempt_paths={"/healthz"})
    return app
