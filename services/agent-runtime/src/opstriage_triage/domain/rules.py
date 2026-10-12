"""Deterministic decisions: the Module 1 stub classifier and the fallback when the decision model fails."""

from __future__ import annotations

from opstriage_contracts.problems import ProblemEvent
from opstriage_contracts.tools import ResolveTargetResult
from opstriage_contracts.triage import Action, DecisionSource, TriageDecision

RESTARTABLE_CATEGORIES = frozenset({"AVAILABILITY", "ERROR"})


def rules_decision(event: ProblemEvent, resolved: ResolveTargetResult | None) -> TriageDecision:
    """AVAILABILITY or ERROR with a container root cause and a resolved target -> restart; else notify.

    The values clear the example thresholds on purpose so the Module 1 slice exercises remediation.
    """
    root = event.root_cause_entity
    has_target = bool(resolved and resolved.target and not resolved.ambiguous)
    if (
        event.category in RESTARTABLE_CATEGORIES
        and root is not None
        and root.type == "container"
        and has_target
    ):
        return TriageDecision(
            source=DecisionSource.RULES,
            action=Action.RESTART_CONTAINER,
            rule_id="rules-only:restart",
            action_confidence=0.95,
            auto_remediable_probability=0.90,
            noise_probability=0.05,
            severity_score=1.5,
        )
    return TriageDecision(
        source=DecisionSource.RULES,
        action=Action.NOTIFY_ONLY,
        rule_id="rules-only:notify",
        action_confidence=0.90,
        severity_score=1.0,
    )


def fallback_decision(event: ProblemEvent, resolved: ResolveTargetResult | None) -> TriageDecision:
    """Used when the decision model fails. It never results in a restart (spec 12.4.4 rule 4)."""
    decision = rules_decision(event, resolved)
    if decision.action is Action.RESTART_CONTAINER:
        return TriageDecision(
            source=DecisionSource.RULES,
            action=Action.NOTIFY_ONLY,
            rule_id="decision-model-fallback",
            action_confidence=decision.action_confidence,
            severity_score=decision.severity_score,
        )
    return decision.model_copy(update={"rule_id": "decision-model-fallback"})
