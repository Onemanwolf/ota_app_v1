import json
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel, ValidationError

from opstriage_llm import ChatJsonClient, LlmEndpoint, LlmInvalidOutput, LlmUnavailable, auth_for

SCHEMA = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}


class Answer(BaseModel):
    n: int


def reply(content: Any) -> httpx2.Response:
    return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})


async def run(handler: Any, seen: list[dict[str, Any]], **endpoint: Any) -> Any:
    def wrapped(request: httpx2.Request) -> httpx2.Response:
        seen.append(
            {"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)}
        )
        return handler(request, len(seen))  # type: ignore[no-any-return]

    e = LlmEndpoint(base_url="http://model:8000", model="m1", **endpoint)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped)) as http:
        return await ChatJsonClient(http, e).complete_json("sys", "user", SCHEMA, Answer.model_validate)


@pytest.mark.parametrize(
    ("mode", "response_format", "schema_in_prompt"),
    [
        ("json_schema", {"type": "json_schema"}, False),
        ("json_object", {"type": "json_object"}, True),
        ("none", None, True),
    ],
)
async def test_request_body_per_structured_output_mode(
    mode: str, response_format: Any, schema_in_prompt: bool
) -> None:
    seen: list[dict[str, Any]] = []
    result = await run(lambda r, n: reply('{"n": 3}'), seen, response_format=mode)
    body = seen[0]["body"]
    assert result.value.n == 3 and result.model == "m1" and result.calls == 1
    assert seen[0]["url"] == "http://model:8000/v1/chat/completions"
    assert body["model"] == "m1" and body["temperature"] == 0
    if response_format is None:
        assert "response_format" not in body
    else:
        assert body["response_format"]["type"] == response_format["type"]
    assert ("JSON Schema" in body["messages"][0]["content"]) is schema_in_prompt


async def test_extra_body_carries_model_specific_fields() -> None:
    seen: list[dict[str, Any]] = []
    await run(
        lambda r, n: reply('{"n": 1}'), seen, extra_body={"chat_template_kwargs": {"enable_thinking": False}}
    )
    assert seen[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_extra_body_cannot_override_core_fields() -> None:
    with pytest.raises(ValidationError, match="extra_body"):
        LlmEndpoint(model="m", extra_body={"model": "other"})


async def test_fenced_json_is_accepted() -> None:
    result = await run(lambda r, n: reply('```json\n{"n": 7}\n```'), [], response_format="none")
    assert result.value.n == 7


async def test_invalid_output_is_repaired_once_then_rejected() -> None:
    seen: list[dict[str, Any]] = []
    result = await run(lambda r, n: reply('{"n": "x"}' if n == 1 else '{"n": 2}'), seen)
    assert result.value.n == 2 and result.calls == 2
    repair = seen[1]["body"]["messages"]
    assert repair[2]["role"] == "assistant" and "problems at: n" in repair[3]["content"]
    with pytest.raises(LlmInvalidOutput):
        await run(lambda r, n: reply("not json"), [])
    with pytest.raises(LlmInvalidOutput):
        await run(lambda r, n: reply('{"n": "x"}'), [], repair_attempts=0)


async def test_server_errors_are_retried_then_unavailable() -> None:
    seen: list[dict[str, Any]] = []
    result = await run(lambda r, n: httpx2.Response(503) if n == 1 else reply('{"n": 5}'), seen)
    assert result.value.n == 5 and len(seen) == 2
    with pytest.raises(LlmUnavailable):
        await run(lambda r, n: httpx2.Response(500), [], attempts=2)
    with pytest.raises(LlmUnavailable):
        await run(lambda r, n: httpx2.Response(401), [])


async def test_odd_response_shapes_are_invalid_output() -> None:
    with pytest.raises(LlmInvalidOutput):
        await run(lambda r, n: httpx2.Response(200, json={"choices": []}), [])
    with pytest.raises(LlmInvalidOutput):
        await run(lambda r, n: reply(None), [])


@pytest.mark.parametrize(
    ("mode", "header", "value"),
    [("bearer", "authorization", "Bearer k"), ("api_key", "api-key", "k")],
)
async def test_auth_modes(mode: str, header: str, value: str) -> None:
    seen: list[dict[str, Any]] = []
    await run(lambda r, n: reply('{"n": 1}'), seen, auth_mode=mode, api_key="k")
    assert seen[0]["headers"][header] == value


def test_key_required_for_key_modes_and_no_auth_by_default() -> None:
    with pytest.raises(ValidationError, match="needs api_key"):
        LlmEndpoint(auth_mode="bearer")
    assert auth_for(LlmEndpoint()) is not None
