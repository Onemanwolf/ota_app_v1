"""Kafka signatures against the canonical fixture scenarios (fixtures/kafka/generate.py)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from opstriage_contracts import dynatrace
from opstriage_contracts.problems import AlertSource, Evidence, ProblemEvent, ProblemStatus
from opstriage_contracts.triage import Action
from opstriage_triage.domain import kafka_signatures
from opstriage_triage.domain.context import ProblemContext

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
INDEX = [
    e
    for e in json.loads((FIXTURES / "kafka/scenarios.json").read_text())
    if "grail" in e["channels"] and e["label"] != "resolve"
]


def context(name: str, **event_updates: Any) -> ProblemContext:
    rec = json.loads((FIXTURES / "dynatrace/grail_records" / f"{name}.json").read_text())
    ctx = json.loads((FIXTURES / "context" / f"{name}.json").read_text())
    event = ProblemEvent(
        source=AlertSource.SIMULATOR,
        problem_id=rec["event.id"],
        display_id=rec["display_id"],
        status=ProblemStatus(rec["event.status"]),
        title=rec["event.name"],
        category=rec["event.category"],
        severity=rec.get("event.severity"),
        started_at=rec["event.start"],
        observed_at=datetime.now(UTC),
        affected_entities=tuple(
            e for e in (dynatrace.smartscape_entity(x) for x in rec["smartscape.affected_entities"]) if e
        ),
        is_duplicate=rec["dt.davis.is_duplicate"],
    ).model_copy(update=event_updates)
    return ProblemContext(
        event=event,
        logs=tuple(ctx["logs"]),
        evidence=tuple(Evidence.model_validate(e) for e in ctx["evidence"]),
    )


DETERMINISTIC = [
    e for e in INDEX if e["id"] not in ("duplicate_problem", "rolling_restart_isr", "log_alert_no_context")
]


@pytest.mark.parametrize("entry", DETERMINISTIC, ids=[e["id"] for e in DETERMINISTIC])
def test_each_scenario_matches_its_signature(entry: dict[str, Any]) -> None:
    result = kafka_signatures.match(context(entry["fixture"]))
    assert result is not None, "no signature matched"
    assert result.action.value == entry["label"], result.signature
    assert result.facts  # every decision says why


def test_no_signature_for_a_lone_log_alert() -> None:
    assert kafka_signatures.match(context("kafka_log_alert_no_context")) is None  # left to the model


def test_disk_full_broker_is_escalated_even_when_titled_process_unavailable() -> None:
    result = kafka_signatures.match(context("kafka_log_dir_failed"))
    assert (
        result is not None
        and result.action is Action.ESCALATE_HUMAN
        and result.signature == "kafka.log-dir-failed"
    )


def test_two_brokers_down_is_not_a_single_instance_restart() -> None:
    ctx = context("kafka_broker_down_single")
    event = ctx.event.model_copy(
        update={
            "affected_entities": (
                *ctx.event.affected_entities,
                dynatrace.smartscape_entity(
                    {"id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E10", "type": "PROCESS_GROUP_INSTANCE"}
                ),
            )
        }
    )
    result = kafka_signatures.match(ctx.model_copy(update={"event": event}))
    assert result is None or result.action is not Action.RESTART_CONTAINER
