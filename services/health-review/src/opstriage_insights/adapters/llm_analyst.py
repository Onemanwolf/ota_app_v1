"""Analyst adapter for any OpenAI-compatible chat model (vLLM/Qwen, Ollama, OpenAI, Azure OpenAI, gateways).

The model, endpoint, auth and structured-output mode come from settings (opstriage_llm.LlmEndpoint). The JSON
schema below is strict-mode compatible (every property required, nullables typed as [type, "null"]).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from opstriage_insights.application.prompts import system_prompt
from opstriage_insights.domain.findings import AnalystOutput, Finding, Recommendation
from opstriage_insights.ports.analyst import AnalystResult, AnalystUnavailable
from opstriage_insights.ports.store import ReviewKind
from opstriage_llm import ChatJsonClient, LlmInvalidOutput, LlmUnavailable

_RISK = ["low", "medium", "high", "critical"]
_LEVEL = ["low", "medium", "high"]
_KIND = ["trend", "potential_issue", "current_issue", "root_cause_hypothesis"]
_CITES = {
    "type": "array",
    "items": {"type": "string", "pattern": "^[BMLP][0-9]+$"},
    "minItems": 1,
    "maxItems": 12,
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "recommendations"],
    "properties": {
        "summary": {"type": "string", "maxLength": 600},
        "findings": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "kind",
                    "title",
                    "detail",
                    "risk",
                    "confidence",
                    "citations",
                    "eta",
                    "confirm_by",
                ],
                "properties": {
                    "kind": {"type": "string", "enum": _KIND},
                    "title": {"type": "string", "maxLength": 120},
                    "detail": {"type": "string", "maxLength": 800},
                    "risk": {"type": "string", "enum": _RISK},
                    "confidence": {"type": "string", "enum": _LEVEL},
                    "citations": _CITES,
                    "eta": {"type": ["string", "null"], "maxLength": 200},
                    "confirm_by": {"type": ["string", "null"], "maxLength": 300},
                },
            },
        },
        "recommendations": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "rationale", "effort", "citations", "addresses"],
                "properties": {
                    "title": {"type": "string", "maxLength": 120},
                    "rationale": {"type": "string", "maxLength": 600},
                    "effort": {"type": "string", "enum": _LEVEL},
                    "citations": _CITES,
                    "addresses": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 0},
                        "maxItems": 12,
                    },
                },
            },
        },
    },
}


class _F(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["trend", "potential_issue", "current_issue", "root_cause_hypothesis"]
    title: str = Field(max_length=200)
    detail: str = Field(max_length=2000)
    risk: Literal["low", "medium", "high", "critical"]
    confidence: Literal["low", "medium", "high"]
    citations: list[str] = Field(max_length=20)
    eta: str | None = Field(default=None, max_length=400)
    confirm_by: str | None = Field(default=None, max_length=600)


class _R(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(max_length=200)
    rationale: str = Field(max_length=1500)
    effort: Literal["low", "medium", "high"]
    citations: list[str] = Field(max_length=20)
    addresses: list[int] = Field(default_factory=list, max_length=20)


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = Field(max_length=1500)
    findings: list[_F] = Field(max_length=20)
    recommendations: list[_R] = Field(default_factory=list, max_length=12)

    def domain(self) -> AnalystOutput:
        return AnalystOutput(
            summary=self.summary,
            findings=tuple(
                Finding(
                    f.kind, f.title, f.detail, f.risk, f.confidence, tuple(f.citations), f.eta, f.confirm_by
                )
                for f in self.findings
            ),
            recommendations=tuple(
                Recommendation(r.title, r.rationale, r.effort, tuple(r.citations), tuple(r.addresses))
                for r in self.recommendations
            ),
        )


class LlmAnalyst:
    def __init__(self, client: ChatJsonClient, runbook: str) -> None:
        self._client, self._runbook = client, runbook

    async def analyse(self, digest_text: str, kind: ReviewKind) -> AnalystResult:
        try:
            result = await self._client.complete_json(
                system_prompt(self._runbook, kind),
                digest_text,
                SCHEMA,
                _Out.model_validate,
                schema_name="review",
            )
        except (LlmUnavailable, LlmInvalidOutput) as exc:
            raise AnalystUnavailable(str(exc)) from exc
        return AnalystResult(result.value.domain(), result.model, result.latency_ms, result.calls)


class ScriptedAnalyst:
    """Fake: returns a fixed output (or raises), for tests and offline runs."""

    def __init__(self, output: AnalystOutput | None = None, *, fail: bool = False) -> None:
        self._output = output or AnalystOutput("Scripted analysis.", (), ())
        self._fail = fail
        self.calls: list[str] = []

    async def analyse(self, digest_text: str, kind: ReviewKind) -> AnalystResult:
        self.calls.append(digest_text)
        if self._fail:
            raise AnalystUnavailable("scripted failure")
        return AnalystResult(self._output, "scripted", 0, 1)
