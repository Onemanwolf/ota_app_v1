"""Triage policy (spec 12.4.4 and 13.2). Pure domain code: pre-route rules and the policy gate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from opstriage_contracts.problems import ProblemEvent, ProblemStatus
from opstriage_contracts.tools import ResolveTargetResult, Severity
from opstriage_contracts.triage import Action, DecisionSource, TriageDecision

Destination = Literal["notify", "ticket", "escalate", "remediate"]
SEVERITY_NAMES = {1: "1 (Critical)", 2: "2 (High)", 3: "3 (Medium)", 4: "4 (Low)", 5: "5 (Informational)"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuleWhen(_Model):
    """All given conditions must hold (AND). Structural matches only: tags, category, flags, exact titles."""

    tag_equals: dict[str, str] = Field(default_factory=dict)
    category_in: tuple[str, ...] = ()
    title_equals: tuple[str, ...] = ()  # exact match, e.g. Dynatrace's "Send test notification" title
    is_duplicate: bool | None = None  # Grail dt.davis.is_duplicate

    def matches(self, event: ProblemEvent) -> bool:
        if self.tag_equals and any(event.tags.get(k) != v for k, v in self.tag_equals.items()):
            return False
        if self.title_equals and event.title not in self.title_equals:
            return False
        if self.is_duplicate is not None and event.is_duplicate != self.is_duplicate:
            return False
        return not self.category_in or event.category in self.category_in


class Rule(_Model):
    id: str
    when: RuleWhen
    action: Action


class RestartThresholds(_Model):
    min_action_confidence: float = Field(default=0.90, ge=0, le=1)
    min_auto_remediable: float = Field(default=0.80, ge=0, le=1)
    max_noise: float = Field(default=0.20, ge=0, le=1)
    max_severity_score: float = 2.5


class TicketThresholds(_Model):
    min_action_confidence: float = Field(default=0.60, ge=0, le=1)


class Thresholds(_Model):
    restart_container: RestartThresholds = RestartThresholds()
    open_ticket: TicketThresholds = TicketThresholds()


class Escalation(_Model):
    on_cannot_tell: bool = True
    on_threshold_failure: bool = True
    severity_score_at_least: float = 2.5


class TicketPolicy(_Model):
    open_when_severity_score_at_least: float = 1.5


class RemediationPolicy(_Model):
    verify_delay_seconds: float = Field(default=30, ge=0)
    execute_poll_max_seconds: float = Field(default=420, ge=1)
    request_dry_run: bool = False  # the remediation server's own dry-run setting still wins


class Policy(_Model):
    version: Literal[1] = 1
    rules: tuple[Rule, ...] = ()
    thresholds: Thresholds = Thresholds()
    escalation: Escalation = Escalation()
    ticket: TicketPolicy = TicketPolicy()
    remediation: RemediationPolicy = RemediationPolicy()

    # ---- pre_route -------------------------------------------------------------------------
    def match_rule(self, event: ProblemEvent, *, has_target: bool) -> Rule | None:
        """First matching rule wins. `has_target` is accepted for rules that need it later (spec 12.2.5)."""
        return next((r for r in self.rules if r.when.matches(event)), None)

    # ---- policy_gate -----------------------------------------------------------------------
    def restart_thresholds_pass(self, d: TriageDecision) -> bool:
        t = self.thresholds.restart_container
        return (
            d.action_confidence is not None
            and d.action_confidence >= t.min_action_confidence
            and d.auto_remediable_probability is not None
            and d.auto_remediable_probability >= t.min_auto_remediable
            and d.noise_probability is not None
            and d.noise_probability <= t.max_noise
            and d.severity_score is not None
            and d.severity_score <= t.max_severity_score
        )

    def plan(
        self, event: ProblemEvent, decision: TriageDecision, resolved: ResolveTargetResult | None
    ) -> list[Destination]:
        """Decision matrix (spec 13.2). The decision can only lower autonomy, never raise it."""
        if event.status is ProblemStatus.CLOSED:
            return []  # CLOSED events are routed to `resolve` before the gate
        if decision.action is Action.SUPPRESS:
            return []  # audit only
        plan: list[Destination] = ["notify"]
        severe = decision.severity_score is not None and (
            decision.severity_score >= self.escalation.severity_score_at_least
        )
        target = resolved.target if resolved else None
        ambiguous = bool(resolved and resolved.ambiguous)

        match decision.action:
            case Action.RESTART_CONTAINER:
                if ambiguous:
                    plan.append("escalate")
                elif target is None or target.autonomy == "notify_only":
                    if severe:
                        plan.append("escalate")
                elif decision.source is DecisionSource.RULES or self.restart_thresholds_pass(decision):
                    # rules/signatures are deterministic and audited by id; model decisions need thresholds
                    plan.append("remediate")
                elif self.escalation.on_threshold_failure:
                    plan.append("escalate")
            case Action.OPEN_TICKET:
                conf = decision.action_confidence
                passes = conf is not None and conf >= self.thresholds.open_ticket.min_action_confidence
                if (
                    decision.source is DecisionSource.RULES or passes
                ):  # rules are audited; models need confidence
                    plan.append("ticket")
            case Action.ESCALATE_HUMAN:
                plan.append("escalate")
            case Action.CANNOT_TELL:
                if self.escalation.on_cannot_tell:
                    plan.append("escalate")
            case Action.NOTIFY_ONLY:
                if severe:
                    plan.append("escalate")

        if ambiguous and "escalate" not in plan:
            plan.append("escalate")  # several targets match: a person must choose (spec 12.5.2)
        wants_ticket = decision.severity_score is not None and (
            decision.severity_score >= self.ticket.open_when_severity_score_at_least
        )
        if wants_ticket and "ticket" not in plan:
            plan.append("ticket")
        return plan

    # ---- notification helpers --------------------------------------------------------------
    def notification_severity(self, event: ProblemEvent, decision: dict[str, Any] | None) -> Severity:
        """Grail event.severity is ITIL-aligned: 1 = Critical ... 5 = Informational (1 is the MOST severe).

        When Dynatrace sets none (always for Problems API v2 and the classic webhook), fall back to the
        documented category order. The classifier's severity score can only raise the result, never lower it.
        """
        if event.severity is not None:
            base = (
                Severity.CRITICAL
                if event.severity <= 2
                else Severity.WARNING
                if event.severity == 3
                else Severity.INFO
            )
        elif event.category in ("MONITORING_UNAVAILABLE", "AVAILABILITY"):
            base = Severity.CRITICAL
        elif event.category in ("ERROR", "SLOWDOWN", "RESOURCE_CONTENTION", "CUSTOM_ALERT"):
            base = Severity.WARNING
        else:
            base = Severity.INFO
        score = (decision or {}).get("severity_score")
        if score is not None and score >= self.escalation.severity_score_at_least:
            return Severity.CRITICAL
        if score is not None and score >= 1.0 and base is Severity.INFO:
            return Severity.WARNING
        return base

    @staticmethod
    def notification_facts(
        event: ProblemEvent, decision: dict[str, Any] | None, target: dict[str, Any] | None
    ) -> dict[str, str]:
        facts = {
            "Problem": event.display_id or event.problem_id,
            "Status": event.status.value,
            "Category": event.category or "n/a",
            "Dynatrace severity": SEVERITY_NAMES.get(event.severity, "not set")
            if event.severity
            else "not set",
        }
        if decision:
            conf = decision.get("action_confidence")
            facts["Decision"] = f"{decision['action']} ({decision['source']}"
            facts["Decision"] += f", confidence {conf:.2f})" if conf is not None else ")"
            if decision.get("model"):
                facts["Model"] = str(decision["model"])
        tgt = (target or {}).get("target")
        if tgt:
            facts["Restart target"] = f"{tgt['target_id']} (autonomy {tgt['autonomy']})"
        elif (target or {}).get("ambiguous"):
            facts["Restart target"] = "ambiguous (several registry entries match)"
        return facts

    @staticmethod
    def notification_links(event: ProblemEvent, run_url: str) -> dict[str, str]:
        links = {"Triage run": run_url}
        if event.problem_url:
            links["Dynatrace problem"] = event.problem_url
        return links
