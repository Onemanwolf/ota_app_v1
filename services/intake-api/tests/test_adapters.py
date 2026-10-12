import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opstriage_contracts.problems import AlertSource, ProblemStatus
from opstriage_intake.adapters import dynatrace_classic, dynatrace_workflow

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "dynatrace"

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def load(rel: str) -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / rel).read_text())  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    "path", sorted(p.name for p in (FIXTURES / "workflow").glob("*.json") if p.stem != "malformed")
)
def test_every_workflow_fixture_maps(path: str) -> None:
    event = dynatrace_workflow.to_problem_event(load(f"workflow/{path}"), now=NOW)
    assert event.source is AlertSource.DYNATRACE_WORKFLOW
    assert event.observed_at == NOW


def test_workflow_container_down_maps_entities_and_dedup_key() -> None:
    event = dynatrace_workflow.to_problem_event(load("workflow/container_down_active.json"), now=NOW)
    assert event.problem_id == "-4821930467193624671_1760263200000V2"
    assert event.display_id == "P-2610121"
    assert event.dedup_key == "-4821930467193624671_1760263200000V2:ACTIVE"
    assert event.root_cause_entity is not None and event.root_cause_entity.name == "demo-orders-api"
    assert [e.type for e in event.affected_entities] == ["container", "host"]


def test_workflow_maintenance_flag_becomes_tag() -> None:
    event = dynatrace_workflow.to_problem_event(load("workflow/maintenance_window_active.json"), now=NOW)
    assert event.tags["maintenance"] == "true"


def test_workflow_closed_has_end() -> None:
    event = dynatrace_workflow.to_problem_event(load("workflow/container_down_closed.json"), now=NOW)
    assert event.status is ProblemStatus.CLOSED and event.ended_at is not None


def test_workflow_malformed_is_rejected() -> None:
    with pytest.raises(ValueError):
        dynatrace_workflow.to_problem_event(load("workflow/malformed.json"), now=NOW)


def test_workflow_unknown_status_is_rejected() -> None:
    body = load("workflow/container_down_active.json") | {"status": "SNOOZED"}
    with pytest.raises(ValueError):
        dynatrace_workflow.to_problem_event(body, now=NOW)


def test_classic_open_maps_from_problem_details() -> None:
    event = dynatrace_classic.to_problem_event(load("classic/container_down_open.json"), now=NOW)
    assert event.source is AlertSource.DYNATRACE_CLASSIC_WEBHOOK
    assert event.status is ProblemStatus.ACTIVE
    assert event.problem_id == "-3141592653589793238_1760263200000V2"
    assert event.display_id == "P-2610127"
    assert event.category == "AVAILABILITY"
    assert event.started_at == datetime(2025, 10, 12, 10, 0, tzinfo=UTC)
    assert event.ended_at is None  # endTime -1 while open
    assert event.root_cause_entity is not None and event.root_cause_entity.type == "container"
    assert event.tags == {"app": "orders", "env": "local", "opstriage": "managed"}


def test_classic_resolved_and_merged_become_closed() -> None:
    resolved = dynatrace_classic.to_problem_event(load("classic/container_down_resolved.json"), now=NOW)
    assert resolved.status is ProblemStatus.CLOSED and resolved.ended_at is not None
    merged = dynatrace_classic.to_problem_event(
        load("classic/container_down_open.json") | {"state": "MERGED"}, now=NOW
    )
    assert merged.status is ProblemStatus.CLOSED and merged.tags["merged"] == "true"


def test_classic_unknown_state_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown classic problem state"):
        dynatrace_classic.to_problem_event(
            load("classic/container_down_open.json") | {"state": "SNOOZED"}, now=NOW
        )
