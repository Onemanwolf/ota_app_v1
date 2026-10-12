"""`opstriage-evals`: record decision-model contract fixtures and run the eval harness (spec 12.4.5).

  uv run opstriage-evals record --base-url http://192.168.5.147:8003
  uv run opstriage-evals run --base-url http://192.168.5.147:8003 --dataset evals/datasets/triage_v1.jsonl \
      --out evals/reports/
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import yaml

from opstriage_contracts.problems import Evidence, ProblemEvent
from opstriage_contracts.tools import ResolveTargetResult, TargetSummary
from opstriage_triage.adapters.auth_headers import no_auth, static_bearer
from opstriage_triage.adapters.systemone import SystemOneDecisionModel
from opstriage_triage.application.eval_metrics import ACTIONS, Prediction, Report, evaluate
from opstriage_triage.domain.context import ProblemContext
from opstriage_triage.domain.state_text import build_state_text
from opstriage_triage.ports import DecisionInput, DecisionModelError

REPO = Path.cwd()
EVAL_TARGET = TargetSummary(
    target_id="eval-target",
    description="eval",
    executor="local_docker",
    autonomy="approval_required",
    container="eval-target",
)


def _model(
    args: argparse.Namespace, http: httpx2.AsyncClient, *, shuffle: bool = False
) -> SystemOneDecisionModel:
    auth = static_bearer(args.api_key) if args.api_key else no_auth()
    return SystemOneDecisionModel(
        http,
        url=f"{args.base_url.rstrip('/')}{args.path}",
        model=args.model,
        auth_header=auth,
        timeout_seconds=args.timeout,
        shuffle_options=shuffle,
    )


def _input(
    event: ProblemEvent,
    logs: list[str],
    has_target: bool,
    max_chars: int,
    evidence: list[dict[str, Any]] | None = None,
) -> DecisionInput:
    resolved = ResolveTargetResult(target=EVAL_TARGET if has_target else None)
    ctx = ProblemContext(
        event=event,
        target=resolved.target,
        logs=tuple(logs),
        evidence=tuple(Evidence.model_validate(e) for e in evidence or []),
    )
    return DecisionInput(context=ctx, resolved=resolved, state_text=build_state_text(ctx, max_chars))


# ------------------------------------------------------------------------------------------------- record
async def record(args: argparse.Namespace) -> None:
    """Capture real provider responses for every workflow fixture (contract fixtures for the parser)."""
    from opstriage_triage.adapters.systemone import parse  # noqa: F401  (documents what the fixtures pin)

    out: Path = args.out
    await asyncio.to_thread(out.mkdir, parents=True, exist_ok=True)
    async with httpx2.AsyncClient() as http:
        model = _model(args, http)
        for path in sorted((REPO / "fixtures/dynatrace/workflow").glob("*_active.json")):
            body = json.loads(path.read_text())
            event = ProblemEvent.model_validate(
                {
                    "source": "simulator",
                    "problem_id": body["event_id"],
                    "display_id": body["display_id"],
                    "status": body["status"],
                    "title": body["name"],
                    "category": body["category"],
                    "severity": body["severity"],
                    "started_at": body["start"],
                    "observed_at": body["start"],
                    "affected_entities": body["affected"],
                    "root_cause_entity": body["root_cause"],
                    "tags": {k: v for k, v in body["tags"].items() if k != "context_fixture"},
                }
            )
            ctx_name = body["tags"].get("context_fixture", "default")
            logs = json.loads((REPO / f"fixtures/context/{ctx_name}.json").read_text())["logs"]
            has_target = event.root_cause_entity is not None and event.root_cause_entity.type == "container"
            request = {
                "model": args.model,
                "state": _input(event, logs, has_target, 12_000).state_text,
                "questions": model.questions(),
            }
            resp = await http.post(
                f"{args.base_url.rstrip('/')}{args.path}", json=request, timeout=args.timeout
            )
            resp.raise_for_status()
            target = out / path.name
            target.write_text(json.dumps({"request": request, "response": resp.json()}, indent=2) + "\n")
            print(f"{target}: {resp.json()['answers']['action']['choice']}")


# ------------------------------------------------------------------------------------------------- run
async def run(args: argparse.Namespace) -> Report:
    cases = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    if args.limit:
        cases = cases[: args.limit]
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx2.AsyncClient() as http:
        plain, shuffled = _model(args, http), _model(args, http, shuffle=True)

        async def one(case: dict[str, Any]) -> Prediction:
            event = ProblemEvent.model_validate(case["event"])
            request = _input(
                event, case["logs"], case["has_target"], args.max_state_chars, case.get("evidence")
            )
            async with sem:
                try:
                    d = await plain.decide(request)
                    s = await shuffled.decide(request) if args.order_check else None
                except DecisionModelError as exc:
                    return Prediction(
                        case["id"],
                        case["label"]["action"],
                        "error",
                        {},
                        None,
                        None,
                        None,
                        None,
                        None,
                        error=str(exc),
                    )
            return Prediction(
                case_id=case["id"],
                label=case["label"]["action"],
                predicted=d.action.value,
                probabilities={k.value: v for k, v in d.action_probabilities.items()},
                confidence=d.action_confidence,
                auto_remediable=d.auto_remediable_probability,
                noise=d.noise_probability,
                severity_score=d.severity_score,
                latency_ms=d.latency_ms,
                shuffled_predicted=s.action.value if s else None,
            )

        preds = await asyncio.gather(*(one(c) for c in cases))
    acceptance = yaml.safe_load(args.acceptance.read_text())
    report = evaluate(preds, float(acceptance["restart_precision_target"]))
    write_report(args, report, preds, acceptance)
    return report


def write_report(
    args: argparse.Namespace, report: Report, preds: list[Prediction], acceptance: dict[str, Any]
) -> None:
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    base = args.out / f"{args.label}-{stamp}"
    args.out.mkdir(parents=True, exist_ok=True)
    data = asdict(report) | {
        "provider": "systemone",
        "model": args.model,
        "endpoint": args.base_url,
        "dataset": str(args.dataset),
        "acceptance": acceptance,
        "generated_at": datetime.now(UTC).isoformat(),
        "predictions": [asdict(p) for p in preds],
    }
    base.with_suffix(".json").write_text(json.dumps(data, indent=2, default=str) + "\n")

    def f(x: float | None, digits: int = 3) -> str:
        return "n/a" if x is None else f"{x:.{digits}f}"

    lines = [
        f"# Decision-model eval: {args.model} ({stamp})",
        "",
        f"Dataset `{args.dataset}` ({report.n} cases, {report.errors} provider errors). Endpoint `{args.base_url}`.",
        "**The dataset is synthetic** (evals/generate_dataset.py); its labels need a human review before these "
        "numbers set production thresholds.",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Accuracy | {f(report.accuracy)} |",
        f"| Macro F1 | {f(report.macro_f1)} |",
        f"| Brier score (action, lower is better) | {f(report.brier)} |",
        f"| Latency p50 / p95 (ms) | {f(report.latency_p50, 0)} / {f(report.latency_p95, 0)} |",
        f"| Confident-error rate, Kev confidence >= 0.9 | {f(report.confident_error_rate['confidence'])} |",
        f"| Confident-error rate, top probability >= 0.9 | {f(report.confident_error_rate['top_probability'])} |",
        f"| Option-order sensitivity (share of changed answers) | {f(report.order_sensitivity)} |",
        "",
        "## Per class",
        "",
        "| Action | Support | Precision | Recall | F1 |",
        "|---|---|---|---|---|",
        *[
            f"| {a} | {int(s['support'])} | {f(s['precision'])} | {f(s['recall'])} | {f(s['f1'])} |"
            for a, s in report.per_class.items()
        ],
        "",
        "## Confusion matrix (rows: label, columns: predicted)",
        "",
        "| label \\ predicted | " + " | ".join(ACTIONS) + " |",
        "|---|" + "---|" * len(ACTIONS),
        *[f"| {a} | " + " | ".join(str(report.confusion[a][b]) for b in ACTIONS) + " |" for a in ACTIONS],
        "",
        f"## Restart threshold sweep (precision target {acceptance['restart_precision_target']})",
        "",
    ]
    for measure, label in (
        ("confidence", "Kev `confidence` (chance-normalised)"),
        ("top_probability", "top option probability"),
    ):
        rec = report.recommendation[measure]
        lines += [
            f"### By {label}",
            "",
            f"Recommendation: **{'none: the target is never met' if rec is None else rec['threshold']}**"
            + ("" if rec is None else f" (precision {f(rec['precision'])}, coverage {f(rec['coverage'])})"),
            "",
            "| Threshold | Automated | Correct | Precision | Coverage |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {p.threshold:.2f} | {p.automated} | {p.correct} | {f(p.precision)} | {f(p.coverage)} |"
            for p in report.sweeps[measure]
            if round(p.threshold * 100) % 5 == 0
        ]
        lines.append("")
    base.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(f"wrote {base}.md and {base}.json")


def main() -> None:
    parser = argparse.ArgumentParser(prog="opstriage-evals")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("record", "run"):
        p = sub.add_parser(name)
        p.add_argument("--base-url", default="http://192.168.5.147:8003")
        p.add_argument("--path", default="/v1/systemone")
        p.add_argument("--model", default="kev-latest")
        p.add_argument("--api-key", default=None)
        p.add_argument("--timeout", type=float, default=30)
    rec = sub.choices["record"]
    rec.add_argument("--out", type=Path, default=Path("fixtures/decision/kev"))
    runp = sub.choices["run"]
    runp.add_argument("--dataset", type=Path, default=Path("evals/datasets/triage_v1.jsonl"))
    runp.add_argument("--acceptance", type=Path, default=Path("evals/acceptance.yaml"))
    runp.add_argument("--out", type=Path, default=Path("evals/reports"))
    runp.add_argument("--label", default="kev-4b")
    runp.add_argument("--limit", type=int, default=0)
    runp.add_argument("--concurrency", type=int, default=4)
    runp.add_argument("--max-state-chars", type=int, default=12_000)
    runp.add_argument("--no-order-check", dest="order_check", action="store_false")
    args = parser.parse_args()
    asyncio.run(record(args) if args.command == "record" else run(args))
