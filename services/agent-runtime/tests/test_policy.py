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
