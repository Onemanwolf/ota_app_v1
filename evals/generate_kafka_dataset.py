"""Generate evals/datasets/triage_kafka_v2.jsonl: 150 labelled Kafka cases built from fixtures/kafka scenarios.

Run: uv run python evals/generate_kafka_dataset.py   (deterministic seed)

Each case starts from a scenario in fixtures/kafka/generate.py (documented Dynatrace shapes, Apache Kafka extension
metric keys, Kafka 3.7 log strings) and varies what changes between real incidents while keeping the Kafka
signature that decides the label:
  - which broker is involved (all broker IDs/names permuted together)
  - metric magnitudes (scaled; sign-critical values such as 0 offline partitions, controller 1 -> 0, leader -1 kept)
  - the Dynatrace title (generic titles such as "Multiple infrastructure problems" are common in practice)
  - root cause present (~20%, real tenants: ~23%) and event.severity null (~25%)
  - evidence path: on the event (Problems API v2 / classic), from the Grail context source, or absent (Workflow
    without enrichment, ~10%)
Events are produced by the real adapters (Workflow and Problems API v2 mappers), so the dataset exercises them.
Labels follow the research report's signature table and are INFERRED from cited runbooks: review before trusting.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import random
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from opstriage_intake.adapters import dynatrace_workflow
from opstriage_poller.adapters.classic import map_problem

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "datasets" / "triage_kafka_v2.jsonl"
RNG = random.Random(20261012)  # noqa: S311 (reproducible test data)
NOW = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)

spec = importlib.util.spec_from_file_location("kafka_fixtures", ROOT / "fixtures" / "kafka" / "generate.py")
if spec is None or spec.loader is None:
    raise SystemExit("cannot load fixtures/kafka/generate.py")
fx = importlib.util.module_from_spec(spec)
sys.modules["kafka_fixtures"] = fx  # dataclasses resolve annotations via sys.modules
spec.loader.exec_module(fx)

# scenario id -> (count, alternative Dynatrace titles seen for the same signature)
PLAN: dict[str, tuple[int, list[str]]] = {
    "broker_down_single": (
        12,
        ["Process unavailable", "Multiple infrastructure problems", "Container restarts"],
    ),
    "broker_oom": (8, ["Out-of-memory kills", "Container restarts", "Multiple infrastructure problems"]),
    "consumer_stuck": (10, ["Kafka consumer lag high", "Unexpected low load"]),
    "rolling_restart_isr": (
        8,
        ["Multiple infrastructure problems", "Process unavailable", "Kafka under-replicated partitions"],
    ),
    "rebalance_during_deploy": (8, ["Unexpected low load", "Kafka consumer lag high"]),
    "duplicate_problem": (4, ["Process unavailable"]),
    "urp_persistent": (8, ["Kafka under-replicated partitions", "Multiple infrastructure problems"]),
    "lag_growth_gradual": (8, ["Kafka consumer lag high"]),
    "low_disk_trending": (8, ["Low disk space", "Kubernetes PVC: Low disk space %"]),
    "ssl_single_client": (6, ["Connectivity problem"]),
    "offline_partitions": (7, ["Kafka offline partitions", "Multiple infrastructure problems"]),
    "controller_lost_kraft": (7, ["Multiple infrastructure problems", "Kafka active controller count not 1"]),
    "log_dir_failed": (8, ["Process unavailable", "Multiple infrastructure problems", "Container restarts"]),
    "unclean_election": (6, ["Kafka unclean leader election"]),
    "ssl_all_clients": (7, ["Failure rate increase", "Connectivity problem"]),
    "gc_long_no_impact": (9, ["Long garbage-collection time", "Memory saturation"]),
    "node_disconnect_lone": (9, ["Connectivity problem"]),
    "log_alert_no_context": (9, ["Kafka error log"]),
}
EXTRA_SUPPRESS = 8  # test notifications + transient NOT_LEADER_OR_FOLLOWER bursts (built below)


def permute_brokers(s: Any) -> Any:
    """Rename all brokers together (ids, names, hosts, pods, log text) with a random permutation of 0-2."""
    perm = RNG.sample([0, 1, 2], 3)
    text = json.dumps(_as_dict(s))
    for n in range(3):
        text = (
            text.replace(f"6A1E3F9C2B7D4E1{n}", f"@PGI{n}@")
            .replace(f"kafka-{n}", f"@K{n}@")
            .replace(f"aks-kafka-pool-000{n}", f"@H{n}@")
            .replace(f"91C2D4E5F6A7081{n}", f"@HID{n}@")
            .replace(f"broker={n}]", f"@B{n}@")
            .replace(f"nodeId={n}]", f"@N{n}@")
        )
    for n, m in enumerate(perm):
        text = (
            text.replace(f"@PGI{n}@", f"6A1E3F9C2B7D4E1{m}")
            .replace(f"@K{n}@", f"kafka-{m}")
            .replace(f"@H{n}@", f"aks-kafka-pool-000{m}")
            .replace(f"@HID{n}@", f"91C2D4E5F6A7081{m}")
            .replace(f"@B{n}@", f"broker={m}]")
            .replace(f"@N{n}@", f"nodeId={m}]")
        )
    return _from_dict(json.loads(text), s)


def _as_dict(s: Any) -> dict[str, Any]:
    return {
        "affected": s.affected,
        "root_cause": s.root_cause,
        "logs": s.logs,
        "events": [vars(e) for e in s.events],
        "metrics": [vars(m) for m in s.metrics],
        "restart_target": s.restart_target,
    }


def _from_dict(d: dict[str, Any], s: Any) -> Any:
    s = copy.deepcopy(s)
    s.affected, s.root_cause, s.logs = d["affected"], d["root_cause"], d["logs"]
    s.events = [fx.Event(**e) for e in d["events"]]
    s.metrics = [fx.Metric(**m) for m in d["metrics"]]
    if d["restart_target"] and d["restart_target"].startswith("kafka-broker-") and s.root_cause:
        s.restart_target = "kafka-broker-" + s.root_cause["name"].split("kafka-")[-1].rstrip(")")
    return s


def jitter(s: Any) -> Any:
    for m in s.metrics:
        if m.before == m.after or m.after in (0, 1, -1, 100) or m.before in (-1,):  # sign-critical / full
            continue
        factor = RNG.uniform(0.5, 2.5)
        m.after = (
            round(m.after * factor, 1)
            if isinstance(m.after, float) and m.after < 10
            else round(m.after * factor)
        )
        if m.unit == "Percent":
            m.after = min(100, max(m.before + 1, round(m.after / factor + RNG.uniform(-4, 6), 1)))
    return s


def _restamp(line: str, at: datetime) -> str:
    """Move a `[yyyy-MM-dd HH:mm:ss,SSS]` log line to the incident's own time."""
    return (
        f"[{at:%Y-%m-%d %H:%M:%S},{at.microsecond // 1000:03d}]" + line[25:]
        if line.startswith("[20")
        else line
    )


def build_case(i: int, base: Any, titles: list[str]) -> dict[str, Any]:
    s = jitter(permute_brokers(copy.deepcopy(base)))
    s.minute = 200 + i * 7  # unique ids per case
    s.title = RNG.choice(titles)
    if s.root_cause and RNG.random() > 0.35:
        s.root_cause = None  # most real problems carry none
    elif not s.root_cause and s.label in ("restart_container", "escalate_human") and RNG.random() < 0.15:
        s.root_cause = s.affected[0]
    if RNG.random() < 0.25:
        s.severity = None
    s.logs = [_restamp(line, s.start - timedelta(seconds=40 - 9 * j)) for j, line in enumerate(s.logs)]
    path = RNG.choices(["problems_v2", "grail_context", "workflow_bare"], weights=[45, 45, 10])[0]
    rec = fx.grail_record(s)
    if path == "problems_v2":
        event = map_problem(fx.v2_problem(s), NOW)
        context_evidence: list[dict[str, Any]] = []
    else:
        event = dynatrace_workflow.to_problem_event(fx.workflow_body(rec, s), now=NOW)
        event = event.model_copy(
            update={"tags": {k: v for k, v in event.tags.items() if k != "context_fixture"}}
        )
        context_evidence = fx.canonical_evidence(s) if path == "grail_context" else []
    has_target = (
        bool(s.restart_target) and len([e for e in s.affected if e["type"] == "PROCESS_GROUP_INSTANCE"]) == 1
    )
    return {
        "id": f"k-{i:03d}",
        "scenario": base.id,
        "evidence_path": path,
        "event": event.model_dump(mode="json"),
        "logs": s.logs,
        "evidence": context_evidence,
        "has_target": has_target,
        "label": {"action": s.label, "signature": base.signature},
    }


def extra_suppress(i: int, k: int) -> dict[str, Any]:
    """Test notifications and a single transient NOT_LEADER_OR_FOLLOWER burst (both: suppress)."""
    if k % 2 == 0:
        from opstriage_intake.adapters import dynatrace_classic

        event = dynatrace_classic.to_problem_event(fx.TEST_NOTIFICATION, now=NOW)
        return {
            "id": f"k-{i:03d}",
            "scenario": "test_notification",
            "evidence_path": "classic",
            "event": event.model_dump(mode="json"),
            "logs": [],
            "evidence": [],
            "has_target": False,
            "label": {"action": "suppress", "signature": "Dynatrace 'Send test notification' payload"},
        }
    s = copy.deepcopy(next(x for x in fx.SCENARIOS if x.id == "node_disconnect_lone"))
    s.minute, s.title, s.label = 900 + k, "Failure rate increase", "suppress"
    s.events = [fx.Event("Failure rate increase", "SERVICE_ERROR_RATE_INCREASED", fx.PRODUCER_SVC)]
    s.logs = [
        fx.log(
            900 + k,
            "WARN",
            "[Producer clientId=checkout-producer-1] Received invalid metadata error in produce "
            "request on partition orders-3 due to org.apache.kafka.common.errors.NotLeaderOrFollowerException: "
            "For requests intended only for the leader, this error indicates that the broker is not the current "
            "leader. Going to request metadata update now",
            "org.apache.kafka.clients.producer.internals.Sender",
        )
    ]
    s.metrics = [
        fx.Metric(
            "Kafka Server - Failed produce requests",
            "kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec.OneMinuteRate",
            "PerSecond",
            0,
            0,
            fx.broker(0),
        )
    ]
    event = map_problem(fx.v2_problem(s), NOW)
    return {
        "id": f"k-{i:03d}",
        "scenario": "transient_not_leader",
        "evidence_path": "problems_v2",
        "event": event.model_dump(mode="json"),
        "logs": s.logs,
        "evidence": [],
        "has_target": False,
        "label": {
            "action": "suppress",
            "signature": "single transient NOT_LEADER_OR_FOLLOWER burst, no lasting failures",
        },
    }


def main() -> None:
    by_id = {s.id: s for s in fx.SCENARIOS}
    cases: list[dict[str, Any]] = []
    i = 1
    for sid, (count, titles) in PLAN.items():
        for _ in range(count):
            cases.append(build_case(i, by_id[sid], titles))
            i += 1
    for k in range(EXTRA_SUPPRESS):
        cases.append(extra_suppress(i, k))
        i += 1
    RNG.shuffle(cases)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case) + "\n")
    labels = Counter(c["label"]["action"] for c in cases)
    paths = Counter(c["evidence_path"] for c in cases)
    roots = sum(1 for c in cases if c["event"]["root_cause_entity"])
    sev_null = sum(1 for c in cases if c["event"]["severity"] is None)
    print(f"{OUT}: {len(cases)} cases {dict(sorted(labels.items()))}")
    print(
        f"evidence paths {dict(paths)}; root cause {roots}/{len(cases)}; severity null {sev_null}/{len(cases)}"
    )


if __name__ == "__main__":
    main()
