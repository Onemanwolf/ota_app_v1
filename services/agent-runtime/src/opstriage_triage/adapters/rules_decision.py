from __future__ import annotations

from opstriage_contracts.triage import TriageDecision
from opstriage_triage.domain.rules import rules_decision
from opstriage_triage.ports import DecisionInput


class RulesOnlyDecisionModel:
    """DecisionModel adapter for TRIAGE__DECISION__PROVIDER=rules (Module 1 stub and offline fallback)."""

    async def decide(self, request: DecisionInput) -> TriageDecision:
        return rules_decision(request.context.event, request.resolved)
