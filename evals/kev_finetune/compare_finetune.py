"""Score the fine-tuned Kev on the Kafka v2 eval set and add it to the existing strategy comparison.

Run: uv run python evals/kev_finetune/compare_finetune.py --kev http://127.0.0.1:18005/v1/systemone
Reads A-F from --baseline (default evals/reports/strategy-comparison-2026-10-12.json).
Writes evals/reports/kev-finetune-comparison-<date>.{md,json}.

Adds, behind the same production pre-route rules as strategies A-F (predictions read from --baseline):
  G   kev-ft          fine-tuned Kev, the production six-way choice question (argmax)
  H   sig+kev-ft      signatures first, G for the rest
  H*  sig+kev-ft gate H with the production confidence gate on model restarts (p < 0.90 -> escalate_human)
Caveat: the training data and this eval set come from the same scenario generator (different seeds), so G
and H measure in-distribution fit, not generalisation to real tenant data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from opstriage_contracts.problems import ProblemEvent
from opstriage_triage.bootstrap import load_policy
from opstriage_triage.domain import kafka_signatures
from opstriage_triage.domain.questions import QUESTIONS
from opstriage_triage.entrypoints.evals import _input

ROOT = Path(__file__).resolve().parents[2]
RESTART_MIN_CONFIDENCE = 0.90  # policy thresholds.restart_container.min_action_confidence


def _load_compare() -> Any:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("compare_strategies", ROOT / "evals/compare_strategies.py")
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load compare_strategies.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["compare_strategies"] = module
    spec.loader.exec_module(module)
    return module


async def run(kev: str, baseline: dict[str, Any], concurrency: int) -> dict[str, Any]:
    rows = [json.loads(x) for x in (ROOT / "evals/datasets/triage_kafka_v2.jsonl").read_text().splitlines()]
    prior = {r["id"]: r for r in baseline["results"]}
    policy = load_policy(ROOT / "config/policy.example.yaml")
    sem = asyncio.Semaphore(concurrency)
    timings: list[float] = []

    async def one(http: httpx2.AsyncClient, row: dict[str, Any]) -> dict[str, Any]:
        event = ProblemEvent.model_validate(row["event"])
        req = _input(event, row["logs"], row["has_target"], 12_000, row["evidence"])
        rule = policy.match_rule(event, has_target=row["has_target"])
        sig = kafka_signatures.match(req.context)
        body = {"model": "kev-latest", "state": req.state_text, "questions": {"action": QUESTIONS["action"]}}
        async with sem:
            t = time.perf_counter()
            resp = await http.post(kev, json=body, timeout=60)
            timings.append(time.perf_counter() - t)
        resp.raise_for_status()
        action = resp.json()["answers"]["action"]
        g, probs = action["choice"], {k: float(v) for k, v in action.get("probabilities", {}).items()}
        conf = float(action.get("confidence", probs.get(g, 0.0)))  # same fallback as adapters/systemone.py
        gated = "escalate_human" if g == "restart_container" and conf < RESTART_MIN_CONFIDENCE else g
        pre = rule.action.value if rule else None
        preds = dict(prior[row["id"]]["preds"])
        preds |= {
            "G kev-ft": pre or g,
            "H sig+kev-ft": pre or (sig.action.value if sig else g),
            "H* sig+kev-ft gate": pre or (sig.action.value if sig else gated),
        }
        return {**prior[row["id"]], "preds": preds, "kev_ft": {"confidence": conf, "probabilities": probs}}

    async with httpx2.AsyncClient() as http:
        results = await asyncio.gather(*(one(http, r) for r in rows))
    return {"results": results, "timings": {"kev-ft": sorted(timings)}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kev", required=True, help="System One URL of the fine-tuned Kev")
    parser.add_argument(
        "--baseline", type=Path, default=ROOT / "evals/reports/strategy-comparison-2026-10-12.json"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    cs = _load_compare()
    baseline = json.loads(args.baseline.read_text())
    data = asyncio.run(run(args.kev, baseline, args.concurrency))
    md = cs.report(data).replace("# Triage strategy comparison", "# Fine-tuned Kev vs triage strategies", 1)
    md += (
        "\n## Notes\n\n- A-F predictions are reused from "
        f"`{args.baseline.relative_to(ROOT)}`; G/H were scored now against the fine-tuned checkpoint.\n"
        "- The fine-tune's training records and this eval set come from the same scenario generator "
        "(different seeds and minimal pairs), so G/H are in-distribution upper bounds, like C and F.\n"
    )
    base = ROOT / "evals/reports" / f"kev-finetune-comparison-{datetime.now(UTC):%Y-%m-%d}"
    base.with_suffix(".json").write_text(json.dumps(data, indent=2) + "\n")
    base.with_suffix(".md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
