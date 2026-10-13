"""Build Kev fine-tuning records for the Dynatrace + Kafka triage decision.

Kev data guide, source 4 (programmatic). Run: uv run python evals/kev_finetune/build_records.py
Writes evals/kev_finetune/data/kafka_triage.jsonl (split into train/calibration/development on the
Spark with Kev's split_data.py) and evals/kev_finetune/data/kafka_v2_dev.jsonl (the 150-case eval
set in Kev format).

Records use the EXACT production question (QUESTIONS["action"]): a fine-tune binds those strings.
States are the production state text, compacted to Kev's ~1400 character limit (logs cut first).
Built from fixtures/kafka scenarios with a different seed than the eval set, plus minimal pairs
(one fact changed, label flips) so the model learns what the decision depends on.
Labels are INFERRED from the research runbook.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from opstriage_contracts.problems import ProblemEvent
from opstriage_triage.domain.questions import QUESTIONS
from opstriage_triage.entrypoints.evals import _input

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "data"
MAX_STATE_CHARS = 1400
PER_LABEL = 190


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gkd = _load("generate_kafka_dataset", ROOT / "evals" / "generate_kafka_dataset.py")
fx = gkd.fx
gkd.RNG.seed(424242)  # different seed from the eval set (20261012)
RNG = gkd.RNG


def record(case: dict[str, Any]) -> dict[str, Any]:
    event = ProblemEvent.model_validate(case["event"])
    state = _input(event, case["logs"], case["has_target"], MAX_STATE_CHARS, case["evidence"]).state_text
    question = {**QUESTIONS["action"], "label": case["label"]["action"]}
    return {
        "state": state,
        "questions": {"action": question},
        "meta": {"scenario": case["scenario"], "evidence_path": case["evidence_path"]},
    }


def minimal_pairs(i0: int, n_each: int) -> list[dict[str, Any]]:
    """Same incident, one fact changed, label flips."""
    by_id = {s.id: s for s in fx.SCENARIOS}
    out: list[dict[str, Any]] = []
    i = i0

    def emit(base: Any, mutate: Any, label: str, titles: list[str], tag: str) -> None:
        nonlocal i
        s = copy.deepcopy(base)
        mutate(s)
        s.label = label
        case = gkd.build_case(i, s, titles)
        case["scenario"] = tag
        out.append(case)
        i += 1

    down, disk, ssl = by_id["broker_down_single"], by_id["low_disk_trending"], by_id["ssl_single_client"]

    def offline(s: Any) -> None:
        for m in s.metrics:
            if m.key == fx.OFFLINE:
                m.after = RNG.randint(1, 40)

    for _ in range(n_each):
        # one broker down -> restart; offline partitions -> escalate
        emit(
            down,
            offline,
            "escalate_human",
            ["Process unavailable", "Multiple infrastructure problems"],
            "pair.down+offline",
        )
        # one broker down -> restart; two brokers down -> escalate (affected set includes another broker)
        emit(
            down,
            lambda s: s.affected.append(fx.broker(0)),
            "escalate_human",
            ["Multiple infrastructure problems", "Process unavailable"],
            "pair.two-brokers-down",
        )

        # disk trending (ticket) -> disk full with log-dir failure (escalate)
        def full(s: Any) -> None:
            for m in s.metrics:
                m.after = 100
            s.logs = [
                fx.log(
                    48,
                    "ERROR",
                    "Error while writing to checkpoint file /var/lib/kafka/data/"
                    "replication-offset-checkpoint: java.io.IOException: No space left on device",
                    "kafka.server.LogDirFailureChannel",
                )
            ]

        emit(disk, full, "escalate_human", ["Low disk space"], "pair.disk-full")

        # SSL from one client (ticket) -> many clients (escalate)
        def many(s: Any) -> None:
            s.logs = [
                fx.log(
                    54,
                    "INFO",
                    f"[SocketServer listenerType=BROKER, nodeId=0] Failed authentication with "
                    f"/10.42.{RNG.randint(1, 9)}.{RNG.randint(2, 250)} (SSL handshake failed)",
                    "org.apache.kafka.common.network.Selector",
                )
                for _ in range(RNG.randint(3, 6))
            ]

        emit(ssl, many, "escalate_human", ["Connectivity problem", "Failure rate increase"], "pair.ssl-many")

        # broker down during maintenance -> suppress
        def maintenance(s: Any) -> None:
            s.maintenance = True
            s.tags = {**s.tags, "maintenance": "true"}

        emit(
            down,
            maintenance,
            "suppress",
            ["Process unavailable", "Multiple infrastructure problems"],
            "pair.down+maintenance",
        )
    return out


def main() -> None:
    by_id = {s.id: s for s in fx.SCENARIOS}
    cases: list[dict[str, Any]] = []
    i = 5000
    # balance labels: ~PER_LABEL base cases per label, split across its scenarios by eval-plan share
    per_label: dict[str, int] = Counter()
    for sid, (count, _) in gkd.PLAN.items():
        per_label[by_id[sid].label] += count
    per_label["suppress"] += gkd.EXTRA_SUPPRESS
    for sid, (count, titles) in gkd.PLAN.items():
        for _ in range(round(PER_LABEL * count / per_label[by_id[sid].label])):
            cases.append(gkd.build_case(i, by_id[sid], titles))
            i += 1
    for k in range(round(PER_LABEL * gkd.EXTRA_SUPPRESS / per_label["suppress"])):
        cases.append(gkd.extra_suppress(i, k))
        i += 1
    cases += minimal_pairs(i, n_each=16)
    records = [record(c) for c in cases]
    seen: set[str] = set()
    unique = [r for r in records if not (r["state"] in seen or seen.add(r["state"]))]  # type: ignore[func-returns-value]
    RNG.shuffle(unique)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "kafka_triage.jsonl").open("w", encoding="utf-8") as fh:
        for r in unique:
            fh.write(json.dumps(r) + "\n")
    dev = [
        record(json.loads(line))
        for line in (ROOT / "evals/datasets/triage_kafka_v2.jsonl").read_text().splitlines()
    ]
    with (OUT / "kafka_v2_dev.jsonl").open("w", encoding="utf-8") as fh:
        for r in dev:
            fh.write(json.dumps(r) + "\n")
    labels = Counter(r["questions"]["action"]["label"] for r in unique)
    lengths = sorted(len(r["state"]) for r in unique)
    print(f"{len(unique)} unique training records ({len(records) - len(unique)} duplicate states dropped)")
    print("labels:", dict(sorted(labels.items())))
    print(f"state chars p50 {lengths[len(lengths) // 2]}, max {lengths[-1]} (limit {MAX_STATE_CHARS})")
    print(f"dev (eval v2 in Kev format): {len(dev)} records")


if __name__ == "__main__":
    main()
