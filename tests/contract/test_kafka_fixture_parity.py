"""Every Kafka scenario rendered in four Dynatrace shapes must normalize to the same canonical problem.

Shapes: Workflow webhook (intake), classic webhook with {ProblemDetailsJSONv2} (intake), Grail DQL row (poller),
Problems API v2 (poller). Fixtures: fixtures/kafka/generate.py. Report: reports/Dynatrace Kafka triage data models.md
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from opstriage_contracts.problems import ProblemEvent
from opstriage_intake.adapters import dynatrace_classic, dynatrace_workflow
from opstriage_poller.adapters.classic import map_problem
from opstriage_poller.adapters.grail import map_record

ROOT = Path(__file__).resolve().parents[2] / "fixtures"
INDEX = json.loads((ROOT / "kafka" / "scenarios.json").read_text())
NOW = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)


def load(kind: str, name: str) -> Any:
    return json.loads((ROOT / "dynatrace" / kind / f"{name}.json").read_text())


def shapes(entry: dict[str, Any]) -> dict[str, ProblemEvent]:
    name, out = entry["fixture"], {}
    if "workflow" in entry["channels"]:
        out["workflow"] = dynatrace_workflow.to_problem_event(load("workflow", name), now=NOW)
    if "grail" in entry["channels"]:
        out["grail"] = map_record(load("grail", name)[0])
    if "classic" in entry["channels"]:
        out["classic"] = dynatrace_classic.to_problem_event(load("classic", name), now=NOW)
    if "problems_v2" in entry["channels"]:
        out["problems_v2"] = map_problem(load("problems_v2", name)["problems"][0], NOW)
    return out


def core(e: ProblemEvent) -> dict[str, Any]:
    return {
        "problem_id": e.problem_id,
        "display_id": e.display_id,
        "status": e.status,
        "title": e.title,
        "category": e.category,
        "affected": sorted((a.id, a.type) for a in e.affected_entities),
        "root_cause": e.root_cause_entity.id if e.root_cause_entity else None,
        "impact": e.impact_levels,
        "started_at": e.started_at,
        "ended_at": e.ended_at,
    }


SCENARIOS = [e for e in INDEX if e["id"] != "test_notification"]


@pytest.mark.parametrize("entry", SCENARIOS, ids=[e["id"] for e in SCENARIOS])
def test_all_shapes_normalize_to_the_same_problem(entry: dict[str, Any]) -> None:
    events = shapes(entry)
    assert len(events) == len(entry["channels"])
    reference = core(next(iter(events.values())))
    for name, event in events.items():
        assert core(event) == reference, name
        assert all(a.type == a.type.lower() for a in event.affected_entities), name  # canonical lowercase


@pytest.mark.parametrize("entry", SCENARIOS, ids=[e["id"] for e in SCENARIOS])
def test_severity_and_evidence_only_where_dynatrace_sends_them(entry: dict[str, Any]) -> None:
    events = shapes(entry)
    for name in ("workflow", "grail"):
        if name in events:
            assert events[name].severity == entry["severity"], name  # Grail 1 = Critical, may be null
            assert events[name].evidence == ()  # not on the problem record
    for name in ("classic", "problems_v2"):
        if name in events:
            assert events[name].severity is None, name  # v2 and classic have no numeric severity
            assert events[name].evidence, name  # evidenceDetails / {ProblemDetailsJSONv2}


def test_v2_and_classic_carry_identical_evidence() -> None:
    entry = next(e for e in SCENARIOS if e["id"] == "broker_down_single")
    events = shapes(entry)
    assert events["classic"].evidence == events["problems_v2"].evidence
    urp = next(ev for ev in events["problems_v2"].evidence if ev.evidence_type == "METRIC")
    assert urp.metric_id == "kafka.server.ReplicaManager.UnderReplicatedPartitions" and urp.value_after == 118


def test_duplicate_flag_survives_both_grail_paths() -> None:
    entry = next(e for e in SCENARIOS if e["id"] == "duplicate_problem")
    assert all(e.is_duplicate for e in shapes(entry).values())


def test_closed_scenario_maps_resolved_and_closed() -> None:
    entry = next(e for e in SCENARIOS if e["id"] == "broker_down_closed")
    for name, event in shapes(entry).items():
        assert event.status.value == "CLOSED" and event.ended_at is not None, name


def test_dynatrace_test_notification_parses() -> None:
    event = dynatrace_classic.to_problem_event(load("classic", "kafka_test_notification"), now=NOW)
    assert event.problem_id == "99999" and event.display_id == "999"
    assert event.title == "Dynatrace problem notification test run"
