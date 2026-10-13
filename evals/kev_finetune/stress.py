"""Out-of-template stress test for the fine-tuned Kev (and the base Kev for reference).

Run: uv run python evals/kev_finetune/stress.py --kev http://127.0.0.1:18005/v1/systemone \
         --base http://127.0.0.1:18003/v1/systemone
Writes evals/reports/kev-finetune-stress-<date>.{md,json}.

Applies label-preserving perturbations that the training generator never produces, one at a time and all
together, to the 150 eval states, and reports accuracy per perturbation. A model that learned the triage logic
should hold its accuracy; one that memorised the generator's surface form will drop.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from opstriage_contracts.problems import ProblemEvent
from opstriage_triage.bootstrap import load_policy
from opstriage_triage.domain.questions import QUESTIONS
from opstriage_triage.entrypoints.evals import _input

ROOT = Path(__file__).resolve().parents[2]
RNG = random.Random(7)  # noqa: S311  (reproducible perturbations, not crypto)

BENIGN = [
    "[{ts}] INFO [Log partition=orders-{p}, dir=/var/lib/kafka/data] Rolled new log segment at offset {o} in 3 ms. "
    "(kafka.log.UnifiedLog)",
    "[{ts}] INFO [GroupCoordinator 1]: Member consumer-billing-{p} in group billing has left, removing it from the "
    "group (kafka.coordinator.group.GroupCoordinator)",
    "[{ts}] INFO [LocalLog partition=__consumer_offsets-{p}, dir=/var/lib/kafka/data] Deleting segment files "
    "LogSegment(baseOffset={o}, size=1048576) (kafka.log.LocalLog$)",
]


def rename(state: str) -> str:
    """Different naming scheme for brokers, consumers and hosts."""
    s = re.sub(
        r"kafka-broker \(kafka-(\d)\)",
        lambda m: f"kfk-prod-b{int(m.group(1)) + 1} (broker {m.group(1)})",
        state,
    )
    s = re.sub(r"\bkafka-(\d)\b", lambda m: f"kfk-prod-b{int(m.group(1)) + 1}", s)
    s = s.replace("aks-kafka-pool-", "ip-10-20-31-").replace("orders-consumer", "payments-ingest")
    return s.replace("demo-orders", "shop-checkout")


def tags(state: str) -> str:
    return re.sub(r"^Tags: .*$", "Tags: env=prod, owner=data-platform, tier=1, app=kafka", state, flags=re.M)


def reorder(state: str) -> str:
    lines = state.split("\n")
    idx = [i for i, line in enumerate(lines) if line.startswith("Evidence: ")]
    moved = [lines[i] for i in idx][::-1]
    for i, line in zip(idx, moved, strict=True):
        lines[i] = line
    return "\n".join(lines)


def noise(state: str) -> str:
    """Two harmless INFO lines at the top of the log section (adds a log section if there is none)."""
    extra = [
        BENIGN[RNG.randrange(len(BENIGN))].format(
            ts=f"2026-11-06 08:59:5{k},000", p=RNG.randint(0, 11), o=RNG.randint(10**5, 10**7)
        )
        for k in range(2)
    ]
    marker = "Recent error and warning logs (newest first):\n"
    if marker in state:
        return state.replace(marker, marker + "\n".join(extra) + "\n", 1)
    return state + "\n" + marker + "\n".join(extra)


def timestamps(state: str) -> str:
    """ISO-8601 log timestamps instead of Kafka's log4j default."""
    return re.sub(r"\[(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),(\d{3})\]", r"\1T\2.\3Z", state)


PERTURB: dict[str, Callable[[str], str]] = {
    "none": lambda s: s,
    "rename": rename,
    "tags": tags,
    "reorder": reorder,
    "noise": noise,
    "timestamps": timestamps,
}
PERTURB["all"] = lambda s: timestamps(noise(reorder(tags(rename(s)))))


async def ask(http: httpx2.AsyncClient, url: str, state: str) -> tuple[str, float]:
    body = {"model": "kev-latest", "state": state, "questions": {"action": QUESTIONS["action"]}}
    resp = await http.post(url, json=body, timeout=60)
    resp.raise_for_status()
    a = resp.json()["answers"]["action"]
    return a["choice"], float(a.get("confidence", 0.0))


async def run(models: dict[str, str], concurrency: int) -> dict[str, Any]:
    rows = [json.loads(x) for x in (ROOT / "evals/datasets/triage_kafka_v2.jsonl").read_text().splitlines()]
    policy = load_policy(ROOT / "config/policy.example.yaml")
    # pre-route rules decide some cases before any model; score the model only where it is asked
    cases = []
    for row in rows:
        event = ProblemEvent.model_validate(row["event"])
        if policy.match_rule(event, has_target=row["has_target"]):
            continue
        state = _input(event, row["logs"], row["has_target"], 12_000, row["evidence"]).state_text
        cases.append((row, state))
    sem = asyncio.Semaphore(concurrency)
    out: list[dict[str, Any]] = []

    async def one(
        http: httpx2.AsyncClient, model: str, url: str, name: str, row: dict[str, Any], s: str
    ) -> None:
        async with sem:
            pred, conf = await ask(http, url, PERTURB[name](s))
        out.append(
            {
                "model": model,
                "perturbation": name,
                "id": row["id"],
                "scenario": row["scenario"],
                "label": row["label"]["action"],
                "pred": pred,
                "confidence": conf,
            }
        )

    async with httpx2.AsyncClient() as http:
        await asyncio.gather(
            *(one(http, m, u, n, r, s) for m, u in models.items() for n in PERTURB for r, s in cases)
        )
    return {"cases": len(cases), "rows": out}


def report(data: dict[str, Any]) -> str:
    rows = data["rows"]
    models = sorted({r["model"] for r in rows}, reverse=True)
    lines = [
        f"# Fine-tuned Kev stress test ({datetime.now(UTC):%Y-%m-%d})",
        "",
        f"{data['cases']} model-routed cases from `evals/datasets/triage_kafka_v2.jsonl` (pre-route rule cases "
        "removed). Each perturbation preserves the label and is something the training generator never produces.",
        "",
        "| Perturbation | " + " | ".join(f"{m} accuracy | {m} wrong restarts" for m in models) + " |",
        "|---|" + "---|---|" * len(models),
    ]
    for p in PERTURB:
        cells = []
        for m in models:
            sel = [r for r in rows if r["model"] == m and r["perturbation"] == p]
            acc = sum(r["pred"] == r["label"] for r in sel) / len(sel)
            bad = sum(r["pred"] == "restart_container" != r["label"] for r in sel)
            cells.append(f"{acc:.3f} | {bad}")
        lines.append(f"| {p} | " + " | ".join(cells) + " |")
    lines += ["", "## Errors under `all` (label -> predicted)", ""]
    for m in models:
        err = Counter(
            (r["label"], r["pred"])
            for r in rows
            if r["model"] == m and r["perturbation"] == "all" and r["pred"] != r["label"]
        )
        lines.append(
            f"- **{m}**: " + (", ".join(f"{a}->{b} x{c}" for (a, b), c in err.most_common(8)) or "none")
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kev", required=True, help="fine-tuned Kev System One URL")
    parser.add_argument("--base", default="", help="base Kev System One URL (optional reference)")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    models = {"kev-ft": args.kev} | ({"kev-base": args.base} if args.base else {})
    data = asyncio.run(run(models, args.concurrency))
    md = report(data)
    base = ROOT / "evals/reports" / f"kev-finetune-stress-{datetime.now(UTC):%Y-%m-%d}"
    base.with_suffix(".json").write_text(json.dumps(data, indent=2) + "\n")
    base.with_suffix(".md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
