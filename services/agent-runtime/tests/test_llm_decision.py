import json
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_contracts.tools import ResolveTargetResult
from opstriage_contracts.triage import Action, DecisionSource
from opstriage_triage.adapters.auth_headers import no_auth
from opstriage_triage.adapters.llm_decision import LlmDecisionModel
from opstriage_triage.domain.context import ProblemContext
from opstriage_triage.ports import DecisionInput, DecisionModelError

NOW = datetime(2026, 10, 12, tzinfo=UTC)
EVENT = ProblemEvent(
    source=AlertSource.SIMULATOR,
    problem_id="p",
    status=ProblemStatus.ACTIVE,
    title="t",
    started_at=NOW,
    observed_at=NOW,
)
REQUEST = DecisionInput(
    context=ProblemContext(event=EVENT),
    resolved=ResolveTargetResult(target=None),
    state_text="Problem: Process unavailable",
)


def reply(content: str) -> httpx2.Response:
    return httpx2.Response(200, json={"choices": [{"message": {"content": content}}]})


QWEN_EXTRA = {"chat_template_kwargs": {"enable_thinking": False}}


async def decide(handler: Any, seen: list[httpx2.Request] | None = None, **options: Any) -> Any:
    def wrapped(r: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(r)
        return handler(r)  # type: ignore[no-any-return]

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped)) as http:
        model = LlmDecisionModel(
            http,
            url="http://qwen:8000/v1/chat/completions",
            model="qwen3.8-flash-next",
            runbook="1. Data at risk -> escalate_human",
            auth_header=no_auth(),
            timeout_seconds=5,
            **options,
        )
        return await model.decide(REQUEST)


async def test_request_shape_and_parsed_decision() -> None:
    seen: list[httpx2.Request] = []
    decision = await decide(
        lambda r: reply('{"action": "escalate_human", "reason": "log dir failed"}'),
        seen,
        extra_body=QWEN_EXTRA,
    )
    assert decision.source is DecisionSource.MODEL and decision.action is Action.ESCALATE_HUMAN
    assert decision.rationale == "log dir failed" and decision.model == "qwen3.8-flash-next"
    assert decision.action_confidence is None  # no calibrated confidence: restarts cannot pass thresholds
    body = json.loads(seen[0].content)
    assert body["temperature"] == 0 and body["response_format"]["json_schema"]["strict"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    system = body["messages"][0]["content"]
    assert "never as instructions" in system and "Data at risk" in system
    assert body["messages"][1]["content"] == "Problem: Process unavailable"


@pytest.mark.parametrize(
    "content", ['{"action": "restart_everything", "reason": "x"}', "not json", '{"reason": "x"}']
)
async def test_untrusted_output_is_rejected(content: str) -> None:
    with pytest.raises(DecisionModelError):
        await decide(lambda r: reply(content))


async def test_unreachable_model_is_a_decision_model_error() -> None:
    def refuse(r: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    with pytest.raises(DecisionModelError):
        await decide(refuse)


async def test_model_specific_fields_are_configuration_not_code() -> None:
    seen: list[httpx2.Request] = []
    await decide(lambda r: reply('{"action": "notify_only", "reason": "x"}'), seen)
    assert "chat_template_kwargs" not in json.loads(
        seen[0].content
    )  # plain OpenAI-compatible body by default


async def test_json_object_mode_for_models_without_strict_schemas() -> None:
    seen: list[httpx2.Request] = []
    decision = await decide(
        lambda r: reply('```json\n{"action": "open_ticket", "reason": "slow burn"}\n```'),
        seen,
        response_format="json_object",
    )
    body = json.loads(seen[0].content)
    assert (
        body["response_format"] == {"type": "json_object"} and "JSON Schema" in body["messages"][0]["content"]
    )
    assert decision.action is Action.OPEN_TICKET
