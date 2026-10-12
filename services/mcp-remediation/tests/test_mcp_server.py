"""MCP in-process tests: tool schemas, validation and error paths (spec section 15)."""

from typing import Any

import pytest
from mcp import Client

from opstriage_remediation.application.service import RemediationService
from opstriage_remediation.bootstrap import Container
from opstriage_remediation.entrypoints.server import build_server


class _Started(Container):
    def __init__(self, service: RemediationService) -> None:
        self._service = service
        self._pool = None


async def _call(service: RemediationService, tool: str, args: dict[str, Any]) -> Any:
    async with Client(build_server(_Started(service))) as client:
        return await client.call_tool(tool, args)


async def test_tool_catalog_and_annotations(service: RemediationService) -> None:
    async with Client(build_server(_Started(service))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "list_targets",
        "resolve_target",
        "restart_container",
        "get_remediation",
        "get_target_status",
    }
    assert tools["restart_container"].annotations is not None
    assert tools["restart_container"].annotations.destructive_hint is True
    assert tools["restart_container"].output_schema is not None


async def test_list_targets_wraps_result(service: RemediationService) -> None:
    result = await _call(service, "list_targets", {})
    assert [t["target_id"] for t in result.structured_content["result"]] == [
        "demo-orders-api",
        "demo-orders-worker",
    ]


async def test_invalid_target_id_is_rejected_before_the_handler(service: RemediationService) -> None:
    result = await _call(
        service,
        "restart_container",
        {
            "request": {
                "target_id": "../../etc",
                "idempotency_key": "run-1:restart",
                "problem_id": "p",
                "reason": "r",
            }
        },
    )
    assert result.is_error


@pytest.mark.parametrize(
    ("tool", "args", "message"),
    [
        (
            "restart_container",
            {
                "request": {
                    "target_id": "nope",
                    "idempotency_key": "run-1:restart",
                    "problem_id": "p",
                    "reason": "r",
                }
            },
            "unknown target",
        ),
        ("get_remediation", {"idempotency_key": "missing-key"}, "unknown idempotency_key"),
    ],
)
async def test_tool_errors_reach_the_caller(
    service: RemediationService, tool: str, args: dict[str, Any], message: str
) -> None:
    result = await _call(service, tool, args)
    assert result.is_error and message in result.content[0].text
