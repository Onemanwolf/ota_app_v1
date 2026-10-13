"""Contract tests: the parser is pinned to recorded Kev responses (spec 12.4.1 task 1)."""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from opstriage_contracts.triage import Action, DecisionSource
from opstriage_triage.adapters.auth_headers import no_auth, static_bearer
from opstriage_triage.adapters.systemone import SystemOneDecisionModel, parse
from opstriage_triage.domain.context import ProblemContext
from opstriage_triage.domain.questions import ACTION_OPTIONS, QUESTIONS
from opstriage_triage.ports import DecisionInput, DecisionModelError

DECISION = Path(__file__).resolve().parents[3] / "fixtures" / "decision"
KEV = sorted((DECISION / "kev").glob("*.json"))


def kev_confidence(probs: dict[str, float]) -> float:
    """Kev's documented formula (kev/api.py choice_confidence): chance-normalised top probability."""
    k = len(probs)
    return (max(probs.values()) - 1 / k) / (1 - 1 / k)


@pytest.mark.parametrize("path", KEV, ids=lambda p: p.stem)
def test_parser_accepts_every_recorded_kev_response(path: Path) -> None:
    recorded = json.loads(path.read_text())
    payload = recorded["response"]
    decision = parse(payload, latency_ms=999)
    action = payload["answers"]["action"]
    assert decision.source is DecisionSource.MODEL and decision.model == "kev-latest"
    assert decision.action.value == action["choice"]
    assert set(decision.action_probabilities) == set(Action)  # all six options answered
    assert decision.action_confidence == action["confidence"]
    assert decision.action_confidence == pytest.approx(kev_confidence(action["probabilities"]), abs=2e-4)
    assert isinstance(decision.latency_ms, int)  # Kev sends a float; the contract field is an int
    assert decision.latency_ms == round(payload["latency_ms"])
    assert 0 <= (decision.severity_score or 0) <= 3
    assert decision.auto_remediable_probability is not None and decision.noise_probability is not None
    assert recorded["request"]["questions"] == QUESTIONS  # the fixtures pin the exact questions sent


def test_recorded_fixtures_exist_for_every_active_scenario() -> None:
    assert len(KEV) >= 7


def test_parser_tolerates_the_documented_microsoft_shape() -> None:
    payload = json.loads((DECISION / "microsoft" / "documented_shape.json").read_text())["response"]
    decision = parse(payload, latency_ms=42)
    assert decision.action is Action.ESCALATE_HUMAN
    assert decision.action_probabilities == {} and decision.action_confidence is None
    assert decision.latency_ms == 42 and decision.severity_score == 2.7


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answers": {}},
        {"answers": {"action": {"confidence": 0.9}}},
        {"answers": {"action": {"choice": "reboot_the_world"}}},
        {"answers": {"action": {"choice": "notify_only", "confidence": 7}}},
    ],
)
def test_unparseable_responses_raise_decision_model_error(payload: dict[str, Any]) -> None:
    with pytest.raises(DecisionModelError):
        parse(payload, latency_ms=1)


def _input() -> DecisionInput:
    from datetime import UTC, datetime

    from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
    from opstriage_contracts.tools import ResolveTargetResult

    now = datetime(2026, 10, 12, tzinfo=UTC)
    event = ProblemEvent(
        source=AlertSource.SIMULATOR,
        problem_id="p",
        status=ProblemStatus.ACTIVE,
        title="t",
        started_at=now,
        observed_at=now,
    )
    return DecisionInput(
        context=ProblemContext(event=event),
        resolved=ResolveTargetResult(target=None),
        state_text="Problem: t",
    )


async def _decide(handler: Any, **kw: Any) -> Any:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        model = SystemOneDecisionModel(
            http,
            url="http://kev:8003/v1/systemone",
            model="kev-latest",
            auth_header=kw.pop("auth", no_auth()),
            timeout_seconds=1,
            **kw,
        )
        return await model.decide(_input())


async def test_sends_all_four_questions_in_one_request_with_auth() -> None:
    seen: list[httpx2.Request] = []
    good = json.loads(KEV[0].read_text())["response"]

    def handler(r: httpx2.Request) -> httpx2.Response:
        seen.append(r)
        return httpx2.Response(200, json=good)

    await _decide(handler, auth=static_bearer("k3y"))
    [request] = seen
    body = json.loads(request.content)
    assert set(body["questions"]) == {"action", "severity", "auto_remediable", "is_noise"}
    assert body["model"] == "kev-latest" and body["state"] == "Problem: t"
    assert request.headers["Authorization"] == "Bearer k3y"


async def test_retries_transient_errors_then_succeeds() -> None:
    good = json.loads(KEV[0].read_text())["response"]
    replies = iter([httpx2.Response(503), httpx2.Response(200, json=good)])
    decision = await _decide(lambda r: next(replies))
    assert decision.model == "kev-latest"


@pytest.mark.parametrize(
    "reply",
    [
        lambda r: httpx2.Response(400, json={"detail": "bad"}),
        lambda r: httpx2.Response(503),
        lambda r: httpx2.Response(200, content=b"<html>"),
    ],
)
async def test_failures_become_decision_model_errors(reply: Any) -> None:
    with pytest.raises(DecisionModelError):
        await _decide(reply, attempts=2)


async def test_unreachable_model_is_a_decision_model_error() -> None:
    def refuse(r: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    with pytest.raises(DecisionModelError):
        await _decide(refuse, attempts=1)


def test_shuffle_keeps_the_same_options_and_never_mutates_the_constant() -> None:
    model = SystemOneDecisionModel(
        httpx2.AsyncClient(),
        url="u",
        model="m",
        auth_header=no_auth(),
        timeout_seconds=1,
        shuffle_options=True,
    )
    orders = {tuple(model.questions()["action"]["criteria"]) for _ in range(30)}
    assert len(orders) > 1 and all(set(o) == set(ACTION_OPTIONS) for o in orders)
    assert tuple(QUESTIONS["action"]["criteria"]) == tuple(ACTION_OPTIONS)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "url", "header", "value"),
    [
        (
            {
                "base_url": "http://kev:8000",
                "path": "/v1/systemone",
                "model": "kev-latest",
                "auth_mode": "none",
            },
            "http://kev:8000/v1/systemone",
            None,
            None,
        ),
        (
            {
                "base_url": "https://api.typesafe.ai",
                "path": "/v1/systemone",
                "model": "jev-latest",
                "auth_mode": "bearer",
                "api_key": "jk",
            },
            "https://api.typesafe.ai/v1/systemone",
            "authorization",
            "Bearer jk",
        ),
        (
            {
                "base_url": "https://res.services.ai.azure.com",
                "path": "/providers/microsoft/v1/systemone",
                "model": "my-decision-deployment",
                "auth_mode": "api_key",
                "api_key": "ak",
            },
            "https://res.services.ai.azure.com/providers/microsoft/v1/systemone",
            "api-key",
            "ak",
        ),
    ],
    ids=["kev", "jev", "microsoft-decision-1"],
)
async def test_switching_classifiers_is_configuration_only(
    overrides: dict[str, Any], url: str, header: str | None, value: str | None
) -> None:
    """Kev, Jev and Microsoft-Decision-1 share the System One contract; only endpoint, model and auth differ."""
    from opstriage_triage.bootstrap import build_decision_model
    from opstriage_triage.settings import Settings

    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "model": "m",
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "notify_only",
                        "probabilities": {"notify_only": 0.9},
                    }
                },
            },
        )

    settings = Settings.model_validate(
        {"db": {"dsn": "postgresql://unused"}, "triage": {"decision": {"provider": "systemone", **overrides}}}
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        decision = await build_decision_model(settings, http).decide(_input())
    assert str(seen[0].url) == url
    body = json.loads(seen[0].content)
    assert set(body) == {"model", "state", "questions"} and body["model"] == overrides["model"]
    if header:
        assert seen[0].headers[header] == value
    else:
        assert "authorization" not in seen[0].headers and "api-key" not in seen[0].headers
    assert decision.action is Action.NOTIFY_ONLY and decision.action_confidence == 0.9
