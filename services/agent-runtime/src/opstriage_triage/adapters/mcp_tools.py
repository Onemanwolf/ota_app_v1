"""Agent-side MCP gateways (spec 12.2.6). Tools are called deterministically; no LLM chooses them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, SecretStr

from opstriage_contracts.problems import EntityRef, ProblemEvent
from opstriage_contracts.tools import (
    ContainerStatusResult,
    NotificationRequest,
    NotificationResult,
    RemediationResult,
    ResolveTargetRequest,
    ResolveTargetResult,
    RestartRequest,
    TicketRequest,
    TicketResult,
)
from opstriage_triage.ports import GatewayError

T = TypeVar("T", bound=BaseModel)


class ToolCallFailed(GatewayError):
    def __init__(self, server: str, tool: str, detail: str) -> None:
        super().__init__(f"{server}.{tool} failed: {detail}")
        self.server, self.tool, self.detail = server, tool, detail


@dataclass(frozen=True, slots=True)
class McpEndpoint:
    url: str
    token: SecretStr


class McpToolInvoker:
    """One short-lived session per call (the servers are stateless)."""

    def __init__(self, endpoints: dict[str, McpEndpoint], timeout_seconds: float) -> None:
        self._endpoints = endpoints
        self._timeout = httpx2.Timeout(timeout_seconds)

    async def call(self, server: str, tool: str, arguments: dict[str, Any], result_type: type[T]) -> T:
        ep = self._endpoints[server]
        # Same settings as mcp's create_mcp_http_client, which is only exported from a private module.
        http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {ep.token.get_secret_value()}"},
            timeout=self._timeout,
            follow_redirects=True,
        )
        async with http, Client(streamable_http_client(ep.url, http_client=http)) as client:
            result = await client.call_tool(tool, arguments)
        if result.is_error:
            detail = " ".join(getattr(block, "text", "") for block in result.content)
            raise ToolCallFailed(server, tool, detail)
        return result_type.model_validate(result.structured_content)


class McpNotificationGateway:
    def __init__(self, invoker: McpToolInvoker) -> None:
        self._invoker = invoker

    async def send(self, request: NotificationRequest) -> NotificationResult:
        return await self._invoker.call(
            "notification",
            "send_notification",
            {"request": request.model_dump(mode="json")},
            NotificationResult,
        )


class McpTicketingGateway:
    def __init__(self, invoker: McpToolInvoker) -> None:
        self._invoker = invoker

    async def create(self, request: TicketRequest) -> TicketResult:
        return await self._invoker.call(
            "ticketing", "create_ticket", {"request": request.model_dump(mode="json")}, TicketResult
        )

    async def resolve(self, problem_id: str, resolution: str) -> TicketResult | None:
        try:
            return await self._invoker.call(
                "ticketing",
                "resolve_ticket",
                {"problem_id": problem_id, "resolution": resolution},
                TicketResult,
            )
        except ToolCallFailed as exc:
            if "no open ticket" in exc.detail:
                return None
            raise


class McpRemediationGateway:
    def __init__(self, invoker: McpToolInvoker) -> None:
        self._invoker = invoker

    async def restart(self, request: RestartRequest) -> RemediationResult:
        return await self._invoker.call(
            "remediation",
            "restart_container",
            {"request": request.model_dump(mode="json")},
            RemediationResult,
        )

    async def get_remediation(self, idempotency_key: str) -> RemediationResult:
        return await self._invoker.call(
            "remediation", "get_remediation", {"idempotency_key": idempotency_key}, RemediationResult
        )

    async def target_status(self, target_id: str) -> ContainerStatusResult:
        return await self._invoker.call(
            "remediation", "get_target_status", {"target_id": target_id}, ContainerStatusResult
        )


def entities_for_resolution(event: ProblemEvent) -> tuple[EntityRef, ...]:
    """Root cause first, then affected entities, without duplicates."""
    seen: dict[str, EntityRef] = {}
    for entity in ((event.root_cause_entity,) if event.root_cause_entity else ()) + event.affected_entities:
        seen.setdefault(entity.id, entity)
    return tuple(seen.values())


class McpTargetResolver:
    def __init__(self, invoker: McpToolInvoker) -> None:
        self._invoker = invoker

    async def resolve(self, event: ProblemEvent) -> ResolveTargetResult:
        request = ResolveTargetRequest(problem_id=event.problem_id, entities=entities_for_resolution(event))
        return await self._invoker.call(
            "remediation", "resolve_target", {"request": request.model_dump(mode="json")}, ResolveTargetResult
        )
