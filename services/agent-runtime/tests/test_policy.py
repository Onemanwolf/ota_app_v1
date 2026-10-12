"""policy_gate truth table (spec 13.2)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_contracts.tools import ResolveTargetResult, TargetSummary
from opstriage_contracts.triage import Action, DecisionSource, TriageDecision
from opstriage_triage.bootstrap import load_policy
from opstriage_triage.domain.policy import Policy

POLICY = load_policy(Path(__file__).resolve().parents[3] / "config" / "policy.example.yaml")
NOW = datetime(2026, 10, 12, tzinfo=UTC)
EVENT = ProblemEvent(
    source=AlertSource.SIMULATOR,
    problem_id="p",
    status=ProblemStatus.ACTIVE,
    title="t",
    started_at=NOW,
    observed_at=NOW,
)


def target(autonomy: str = "approval_required") -> ResolveTargetResult:
    return ResolveTargetResult(
        target=TargetSummary(
            target_id="t", description="d", executor="local_docker", autonomy=autonomy, container="c"
        )
    )  # type: ignore[arg-type]


def decision(
    action: Action,
    conf: float = 0.95,
    remediable: float | None = 0.9,
    noise: float | None = 0.05,
    severity: float | None = 1.0,
) -> TriageDecision:
    return TriageDecision(
        source=DecisionSource.MODEL,
        action=action,
        action_confidence=conf,
        auto_remediable_probability=remediable,
        noise_probability=noise,
        severity_score=severity,
    )


NONE = ResolveTargetResult(target=None)
AMBIGUOUS = ResolveTargetResult(target=None, ambiguous=True)


@pytest.mark.parametrize(
    ("d", "resolved", "expected"),
    [
        # restart_container
        (decision(Action.RESTART_CONTAINER), target("autonomous"), ["notify", "remediate"]),
        (decision(Action.RESTART_CONTAINER), target("approval_required"), ["notify", "remediate"]),
        (decision(Action.RESTART_CONTAINER, conf=0.5), target(), ["notify", "escalate"]),
        (decision(Action.RESTART_CONTAINER, remediable=0.5), target(), ["notify", "escalate"]),
        (decision(Action.RESTART_CONTAINER, noise=0.5), target(), ["notify", "escalate"]),
        (decision(Action.RESTART_CONTAINER, severity=2.9), target(), ["notify", "escalate", "ticket"]),
        (decision(Action.RESTART_CONTAINER, remediable=None), target(), ["notify", "escalate"]),
        (decision(Action.RESTART_CONTAINER), target("notify_only"), ["notify"]),
        (
            decision(Action.RESTART_CONTAINER, severity=2.6),
            target("notify_only"),
            ["notify", "escalate", "ticket"],
        ),
        (decision(Action.RESTART_CONTAINER), NONE, ["notify"]),
        (decision(Action.RESTART_CONTAINER), AMBIGUOUS, ["notify", "escalate"]),
        # other actions
        (decision(Action.OPEN_TICKET, conf=0.7), NONE, ["notify", "ticket"]),
        (decision(Action.OPEN_TICKET, conf=0.3), NONE, ["notify"]),
        (decision(Action.ESCALATE_HUMAN), target(), ["notify", "escalate"]),
        (decision(Action.CANNOT_TELL), target(), ["notify", "escalate"]),
        (decision(Action.NOTIFY_ONLY), target(), ["notify"]),
        (decision(Action.NOTIFY_ONLY), AMBIGUOUS, ["notify", "escalate"]),
        (decision(Action.NOTIFY_ONLY, severity=1.6), NONE, ["notify", "ticket"]),
        (decision(Action.SUPPRESS, severity=3.0), target(), []),
    ],
)
def test_plan(d: TriageDecision, resolved: ResolveTargetResult, expected: list[str]) -> None:
    assert POLICY.plan(EVENT, d, resolved) == expected


def test_closed_events_never_plan_actions() -> None:
    closed = EVENT.model_copy(update={"status": ProblemStatus.CLOSED})
    assert POLICY.plan(closed, decision(Action.RESTART_CONTAINER), target("autonomous")) == []


def test_maintenance_rule_matches_tag_only() -> None:
    assert POLICY.match_rule(EVENT, has_target=True) is None
    tagged = EVENT.model_copy(update={"tags": {"maintenance": "true"}})
    rule = POLICY.match_rule(tagged, has_target=True)
    assert rule is not None and rule.action is Action.SUPPRESS


def test_policy_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        Policy.model_validate({"version": 1, "thresholds": {"restart_container": {"min_confidence": 0.1}}})


@pytest.mark.parametrize(
    ("severity", "category", "expected"),
    [
        (1, "CUSTOM_ALERT", "critical"),  # Grail: 1 = Critical
        (2, "ERROR", "critical"),  # 2 = High
        (3, "AVAILABILITY", "warning"),
        (4, "AVAILABILITY", "info"),  # 4 = Low: NOT critical (the old code treated >= 4 as critical)
        (5, "ERROR", "info"),
        (None, "AVAILABILITY", "critical"),  # no severity (v2, classic): documented category order
        (None, "MONITORING_UNAVAILABLE", "critical"),
        (None, "SLOWDOWN", "warning"),
        (None, None, "info"),
    ],
)
def test_notification_severity_follows_grail_direction(
    severity: int | None, category: str | None, expected: str
) -> None:
    event = EVENT.model_copy(update={"severity": severity, "category": category})
    assert POLICY.notification_severity(event, None).value == expected


def test_classifier_severity_can_raise_but_not_lower() -> None:
    low = EVENT.model_copy(update={"severity": 5})
    assert POLICY.notification_severity(low, {"severity_score": 2.9}).value == "critical"
    high = EVENT.model_copy(update={"severity": 1})
    assert POLICY.notification_severity(high, {"severity_score": 0.0}).value == "critical"


def test_test_notification_and_duplicates_are_suppressed_by_rule() -> None:
    test = EVENT.model_copy(update={"title": "Dynatrace problem notification test run"})
    rule = POLICY.match_rule(test, has_target=False)
    assert rule is not None and rule.id == "dynatrace-test-notification" and rule.action is Action.SUPPRESS
    dup = EVENT.model_copy(update={"is_duplicate": True})
    rule = POLICY.match_rule(dup, has_target=False)
    assert rule is not None and rule.id == "duplicate-problem"
    assert (
        POLICY.match_rule(EVENT.model_copy(update={"title": "Process unavailable"}), has_target=True) is None
    )


def test_rule_and_signature_decisions_skip_model_confidence_gates() -> None:
    ticket = TriageDecision(
        source=DecisionSource.RULES, action=Action.OPEN_TICKET, rule_id="kafka.urp-persistent"
    )
    assert POLICY.plan(EVENT, ticket, NONE) == ["notify", "ticket"]
    restart = TriageDecision(
        source=DecisionSource.RULES, action=Action.RESTART_CONTAINER, rule_id="kafka.single-broker-down"
    )
    assert POLICY.plan(EVENT, restart, target()) == ["notify", "remediate"]
    assert POLICY.plan(EVENT, restart, AMBIGUOUS) == ["notify", "escalate"]  # ambiguity still wins
    assert POLICY.plan(EVENT, restart, target("notify_only")) == ["notify"]  # autonomy still wins


def test_model_decisions_without_confidence_cannot_ticket_or_restart() -> None:
    ticket = TriageDecision(source=DecisionSource.MODEL, action=Action.OPEN_TICKET)
    assert POLICY.plan(EVENT, ticket, NONE) == ["notify"]
    restart = TriageDecision(source=DecisionSource.MODEL, action=Action.RESTART_CONTAINER)
    assert POLICY.plan(EVENT, restart, target()) == ["notify", "escalate"]
