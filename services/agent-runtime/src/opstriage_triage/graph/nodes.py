from __future__ import annotations

from typing import Any

import structlog
from langgraph.runtime import Runtime

from opstriage_contracts.problems import ProblemEvent, ProblemStatus
from opstriage_contracts.tools import ResolveTargetResult
from opstriage_contracts.triage import DecisionSource, TriageDecision
from opstriage_triage.domain.context import ProblemContext
from opstriage_triage.domain.rules import fallback_decision
from opstriage_triage.domain.state_text import build_state_text
from opstriage_triage.graph.state import TriageDeps, TriageState
from opstriage_triage.ports import DecisionInput, DecisionModelError

log = structlog.get_logger()


async def load_context(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    """Resolve the restart target first (its container and host IDs scope the log query), then context."""
    deps = runtime.context
    event = ProblemEvent.model_validate(state["event"])
    if event.status is ProblemStatus.CLOSED:
        return {"target": None}  # the resolve path needs neither target nor logs
    resolved = await deps.targets.resolve(event)  # MCP resolve_target: deterministic registry match
    context = await deps.context_source.get_context(event, resolved)
    return {"target": resolved.model_dump(mode="json"), "context": context.model_dump(mode="json")}


async def pre_route(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    event = ProblemEvent.model_validate(state["event"])
    resolved = state.get("target") or {}
    has_target = bool(resolved.get("target")) and not resolved.get("ambiguous", False)
    rule = runtime.context.policy.match_rule(event, has_target=has_target)
    if rule is None:
        return {}
    decision = TriageDecision(source=DecisionSource.RULES, action=rule.action, rule_id=rule.id)
    return {"decision": decision.model_dump(mode="json")}


def after_pre_route(state: TriageState) -> list[str]:
    if state["event"]["status"] == ProblemStatus.CLOSED:
        return ["resolve"]
    return ["policy_gate"] if state.get("decision") else ["classify"]


async def classify(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    deps = runtime.context
    context = ProblemContext.model_validate(state["context"])
    resolved = ResolveTargetResult.model_validate(state.get("target") or {"target": None})
    request = DecisionInput(
        context=context, resolved=resolved, state_text=build_state_text(context, deps.max_state_chars)
    )
    try:
        decision = await deps.decision_model.decide(request)
    except DecisionModelError as exc:
        log.warning("decision_model_failed_using_rules_fallback", error=str(exc))
        decision = fallback_decision(context.event, resolved)
        return {"decision": decision.model_dump(mode="json"), "decision_error": str(exc)[:500]}
    return {"decision": decision.model_dump(mode="json"), "decision_error": None}


async def policy_gate(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    event = ProblemEvent.model_validate(state["event"])
    decision = TriageDecision.model_validate(state["decision"])
    target = state.get("target")
    resolved = ResolveTargetResult.model_validate(target) if target else None
    return {"plan": list(runtime.context.policy.plan(event, decision, resolved))}


def fan_out(state: TriageState) -> list[str]:
    return state.get("plan") or ["finalize"]
