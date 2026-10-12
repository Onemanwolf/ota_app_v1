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

from opstriage_contracts.tools import (
    ContainerStatusResult,
    RemediationResult,
    ResolveTargetRequest,
    ResolveTargetResult,
    RestartRequest,
    TargetSummary,
)
from opstriage_remediation.bootstrap import Container
from opstriage_remediation.domain.errors import PolicyViolation
from opstriage_remediation.settings import Settings
from opstriage_shared.asgi import BearerTokenMiddleware


def build_server(c: Container) -> MCPServer:
    server = MCPServer(
        "opstriage-remediation",
        instructions="Restart allow-listed containers on VMs. Every call is policy-checked and audited.",
    )

    @server.tool(title="List restart targets", annotations=ToolAnnotations(read_only_hint=True))
    async def list_targets() -> list[TargetSummary]:
        return c.service.list_targets()

    @server.tool(title="Resolve restart target", annotations=ToolAnnotations(read_only_hint=True))
    async def resolve_target(request: ResolveTargetRequest) -> ResolveTargetResult:
        return await c.service.resolve_target(request)

    @server.tool(
        title="Restart container",
        annotations=ToolAnnotations(
            read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
        ),
    )
    async def restart_container(request: RestartRequest) -> RemediationResult:
        try:
            return await c.service.restart_container(request)
        except PolicyViolation as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(title="Get remediation", annotations=ToolAnnotations(read_only_hint=True))
    async def get_remediation(idempotency_key: str) -> RemediationResult:
        result = await c.service.get_remediation(idempotency_key)
        if result is None:
            raise ToolError("unknown idempotency_key")
        return result

    @server.tool(title="Get target status", annotations=ToolAnnotations(read_only_hint=True))
    async def get_target_status(target_id: str) -> ContainerStatusResult:
        try:
            return await c.service.get_target_status(target_id)
        except PolicyViolation as exc:
            raise ToolError(str(exc)) from exc

    return server


def build_app(settings: Settings, container: Container) -> Starlette:
    server = build_server(container)

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await container.start()
        try:
            async with server.session_manager.run():  # required, or "Task group is not initialized"
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
