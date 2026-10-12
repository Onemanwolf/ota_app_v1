from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from opstriage_contracts import cli
from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_contracts.tools import RestartRequest
from opstriage_contracts.triage import Action, DecisionSource, TriageDecision

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


def _event(**overrides: object) -> ProblemEvent:
    data: dict[str, object] = {
        "source": AlertSource.SIMULATOR,
        "problem_id": "-482193_1760263200000V2",
        "status": ProblemStatus.ACTIVE,
        "title": "Container down",
        "started_at": NOW,
        "observed_at": NOW,
    }
    data.update(overrides)
    return ProblemEvent.model_validate(data)


def test_dedup_key_is_problem_id_and_status() -> None:
    assert _event().dedup_key == "-482193_1760263200000V2:ACTIVE"
    assert _event(status="CLOSED").dedup_key == "-482193_1760263200000V2:CLOSED"


def test_contracts_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _event(unexpected="x")


def test_contracts_are_frozen() -> None:
    event = _event()
    with pytest.raises(ValidationError):
        event.title = "changed"  # type: ignore[misc]


def test_severity_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _event(severity=6)


@pytest.mark.parametrize("target_id", ["Demo", "-x", "a b", "x" * 64, "../etc"])
def test_restart_target_id_pattern_rejects_unsafe_values(target_id: str) -> None:
    with pytest.raises(ValidationError):
        RestartRequest(target_id=target_id, idempotency_key="run-1:restart", problem_id="p", reason="r")


def test_restart_request_defaults_to_dry_run() -> None:
    req = RestartRequest(
        target_id="demo-orders-api", idempotency_key="run-1:restart", problem_id="p", reason="r"
    )
    assert req.dry_run is True


def test_triage_decision_round_trips_through_json() -> None:
    decision = TriageDecision(
        source=DecisionSource.MODEL,
        action=Action.RESTART_CONTAINER,
        action_probabilities={Action.RESTART_CONTAINER: 0.7, Action.NOTIFY_ONLY: 0.3},
        action_confidence=0.7,
    )
    assert TriageDecision.model_validate(decision.model_dump(mode="json")) == decision


def test_committed_schemas_are_current() -> None:
    assert cli.check(Path(cli.DEFAULT_OUT)) == []


def test_schema_1_0_events_still_validate() -> None:
    event = _event(schema_version="1.0")
    assert event.evidence == () and event.impact_levels == () and not event.is_duplicate


def test_evidence_and_impact_levels() -> None:
    from opstriage_contracts.problems import Evidence

    event = _event(
        impact_levels=("INFRASTRUCTURE",),
        evidence=(
            Evidence(
                evidence_type="METRIC",
                display_name="Kafka broker - Under replicated partitions",
                metric_id="kafka.server.ReplicaManager.UnderReplicatedPartitions",
                unit="Count",
                value_before=0,
                value_after=118,
            ),
        ),
    )
    assert event.schema_version == "1.1" and event.evidence[0].value_after == 118
    with pytest.raises(ValidationError):
        _event(impact_levels=("SERVICES",))  # canonical is SERVICE; adapters normalize
