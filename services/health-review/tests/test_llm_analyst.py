import json
from typing import Any

import httpx2
import pytest

from opstriage_insights.adapters.llm_analyst import SCHEMA, LlmAnalyst
from opstriage_insights.ports.analyst import AnalystUnavailable
from opstriage_llm import ChatJsonClient, LlmEndpoint

GOOD = {
    "summary": "One issue.",
    "findings": [
        {
            "kind": "current_issue",
            "title": "URP on kafka-0",
            "detail": "persists",
            "risk": "medium",
            "confidence": "high",
            "citations": ["B1", "M1"],
            "eta": None,
            "confirm_by": "check fetcher lag",
        }
    ],
    "recommendations": [
        {
            "title": "Check follower",
            "rationale": "lagging",
            "effort": "low",
            "citations": ["M1"],
            "addresses": [0],
        }
    ],
}


def reply(body: Any) -> httpx2.Response:
    return httpx2.Response(200, json={"choices": [{"message": {"content": json.dumps(body)}}]})


async def run(handler: Any, seen: list[Any], **endpoint: Any) -> Any:
    def wrapped(r: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(r.content))
        return handler(len(seen))  # type: ignore[no-any-return]

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped)) as http:
        client = ChatJsonClient(http, LlmEndpoint(base_url="http://any-model", model="any", **endpoint))
        return await LlmAnalyst(client, "RUNBOOK TEXT").analyse("CLUSTER c1 ...", "review")


async def test_maps_output_and_sends_the_review_prompt() -> None:
    seen: list[Any] = []
    result = await run(lambda n: reply(GOOD), seen)
    f = result.output.findings[0]
    assert f.citations == ("B1", "M1") and f.confirm_by == "check fetcher lag" and result.model == "any"
    system = seen[0]["messages"][0]["content"]
    assert "untrusted" in system and "RUNBOOK TEXT" in system and "daily health review" in system
    assert seen[0]["messages"][1]["content"] == "CLUSTER c1 ..."
    assert seen[0]["response_format"]["json_schema"]["schema"] == SCHEMA


def test_schema_is_strict_mode_compatible() -> None:
    def check(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False and set(node["required"]) == set(
                    node["properties"]
                )
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(SCHEMA)


async def test_invalid_output_is_repaired_then_unavailable() -> None:
    seen: list[Any] = []
    result = await run(lambda n: reply({"summary": "x"}) if n == 1 else reply(GOOD), seen)
    assert result.calls == 2
    with pytest.raises(AnalystUnavailable):
        await run(lambda n: reply({"summary": "x"}), [])


@pytest.mark.parametrize("mode", ["json_object", "none"])
async def test_works_with_models_without_strict_schemas(mode: str) -> None:
    seen: list[Any] = []
    result = await run(lambda n: reply(GOOD), seen, response_format=mode)
    assert result.output.summary == "One issue." and "JSON Schema" in seen[0]["messages"][0]["content"]
