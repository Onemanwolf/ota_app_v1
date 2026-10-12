from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from opstriage_contracts.tools import TicketRequest, TicketResult
from opstriage_shared.asgi import BearerTokenMiddleware
from opstriage_ticketing.bootstrap import Container
from opstriage_ticketing.domain.errors import NoOpenTicket
from opstriage_ticketing.settings import Settings


def build_server(c: Container) -> MCPServer:
    server = MCPServer(
        "opstriage-ticketing", instructions="Open and resolve tickets. One open ticket per problem."
    )

    @server.tool(
        title="Create ticket", annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True)
    )
    async def create_ticket(request: TicketRequest) -> TicketResult:
        return await c.tickets.create(request)

    @server.tool(
        title="Resolve ticket", annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True)
    )
    async def resolve_ticket(problem_id: str, resolution: str) -> TicketResult:
        try:
            return await c.tickets.resolve(problem_id, resolution)
        except NoOpenTicket as exc:
            raise ToolError(str(exc)) from exc

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
