"""Root-cause analysis evaluation (OTA-APP-002 5.6 DoD), for ANY analyst model.

Run (Qwen on the Spark through scripts/kev-tunnel.sh):  uv run python evals/rca_eval.py
Takes the same endpoint options as evals/health_review_eval.py. Writes evals/reports/rca-<model>-<date>.{md,json}.

For each of the 10 RCA fixture clusters (fixtures/insights/rca-*), the analysis runs on the problem's window.
A case passes when one of the model's top-2 root-cause hypotheses (by rank) cites evidence matching the planted
cause (rca.root_cause in scenario.json): a metric entry on the right metric and entity ("*" = any entity), a
breach citing it, or a log template containing the text. Target (spec 5.6): at least 8 of 10.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/health-review/tests"))

from insights_testkit import ANCHOR, SCOPES, STORE, collector  # noqa: E402

from opstriage_insights.adapters.evidence_json import serialize  # noqa: E402
from opstriage_insights.adapters.fixtures import FixtureProblemSource  # noqa: E402
from opstriage_insights.adapters.guard import HttpGuard, KeywordGuard  # noqa: E402
from opstriage_insights.adapters.llm_analyst import LlmAnalyst  # noqa: E402
from opstriage_insights.adapters.stores import MemoryReviewStore  # noqa: E402
from opstriage_insights.application.collect import BuildDigest, PrepareReview  # noqa: E402
from opstriage_insights.application.run_rca import PlanRca  # noqa: E402
from opstriage_insights.application.run_review import RunReview  # noqa: E402
from opstriage_insights.domain.retention import RetentionPolicy  # noqa: E402
from opstriage_llm import ChatJsonClient, LlmEndpoint  # noqa: E402


def _matches(ev: dict[str, str], e: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    if e["type"] == "metric" and "metric" in ev:
        return e["metric"] == ev["metric"] and ev["entity"] in ("*", e["entity"])
    if e["type"] == "log" and "log" in ev:
        return bool(e["template"]) and ev["log"] in e["template"]
    if e["type"] == "breach":
        return any(_matches(ev, by_id[c], by_id) for c in e["cites"] if c in by_id)
    return False


def score(cluster: str, report: dict[str, Any]) -> dict[str, Any]:
    rca = STORE.scenario(cluster)["rca"]
    by_id = {e["id"]: e for e in report["evidence"]}
    hyps = sorted(
        (f for f in report["findings"] if f["kind"] == "root_cause_hypothesis"), key=lambda f: f["rank"] or 99
    )
    hit_rank = None
    for f in hyps:
        if any(
            _matches(ev, by_id[c], by_id) for ev in rca["root_cause"] for c in f["citations"] if c in by_id
        ):
            hit_rank = f["rank"]
            break
    return {
        "cluster": cluster,
        "display_id": rca["display_id"],
        "hypotheses": len(hyps),
        "hit_rank": hit_rank,
        "pass": hit_rank is not None and hit_rank <= 2,
        "top": [h["title"] for h in hyps[:3]],
        "analyst_available": report["analyst_available"],
        "latency_ms": report["latency_ms"],
        "dropped_uncited": report["counts"]["dropped_uncited"],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    async with httpx2.AsyncClient() as http:
        guard: Any = KeywordGuard()
        try:
            await HttpGuard(http, args.guard_url).flags(["ping"])
            guard = HttpGuard(http, args.guard_url)
        except Exception:
            print("guard unreachable; using the keyword fake", file=sys.stderr)
        endpoint = LlmEndpoint(
            base_url=args.base_url,
            model=args.model,
            auth_mode=args.auth_mode,
            api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
            response_format=args.response_format,
            extra_body=json.loads(args.extra_body),
            max_tokens=args.max_tokens,
            timeout_seconds=args.timeout,
        )
        analyst = LlmAnalyst(
            ChatJsonClient(http, endpoint), (ROOT / "config/insights-runbook.md").read_text()
        )
        plan_rca = PlanRca(FixtureProblemSource(STORE), dict(SCOPES), lambda: ANCHOR)
        results, reports = [], {}
        for cluster in sorted(c for c in SCOPES if STORE.scenario(c)["kind"] == "rca"):
            plan = await plan_rca(STORE.scenario(cluster)["rca"]["display_id"])
            store = MemoryReviewStore()
            prepare = PrepareReview(
                store, collector(), BuildDigest(guard), RetentionPolicy(), lambda: ANCHOR, uuid4, serialize
            )
            started = time.perf_counter()
            outcome = await RunReview(prepare, analyst, store, None, RetentionPolicy(), lambda: ANCHOR)(
                plan.scope, plan.subject, None, plan.window
            )
            s = score(cluster, outcome.report)
            s["wall_s"] = round(time.perf_counter() - started, 1)
            results.append(s)
            reports[cluster] = outcome.report
            print(
                f"{cluster:24} hit rank {s['hit_rank']} pass {s['pass']} ({s['hypotheses']} hypotheses, "
                f"{s['wall_s']}s)",
                file=sys.stderr,
            )
    return {"model": args.model, "results": results, "reports": reports}


def markdown(data: dict[str, Any]) -> str:
    res = data["results"]
    passed = sum(r["pass"] for r in res)
    lines = [
        f"# Root-cause analysis evaluation: {data['model']} ({datetime.now(UTC):%Y-%m-%d})",
        "",
        f"**{passed} of {len(res)}** cases have the planted cause in the top 2 hypotheses (target: at least 8).",
        "",
        "| Cluster | Problem | Cause at rank | Pass | Top hypotheses |",
        "|---|---|---|---|---|",
    ]
    for r in res:
        lines.append(
            f"| {r['cluster']} | {r['display_id']} | {r['hit_rank'] or '-'} | {'yes' if r['pass'] else 'no'} | "
            f"{'; '.join(r['top']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://127.0.0.1:18001")
    p.add_argument("--model", default="qwen3.8-flash-next")
    p.add_argument("--auth-mode", default="none", choices=["none", "bearer", "api_key"])
    p.add_argument("--api-key-env", default="")
    p.add_argument("--response-format", default="json_schema", choices=["json_schema", "json_object", "none"])
    p.add_argument("--extra-body", default='{"chat_template_kwargs": {"enable_thinking": false}}')
    p.add_argument("--max-tokens", type=int, default=2000)
    p.add_argument("--timeout", type=float, default=240)
    p.add_argument("--guard-url", default="http://127.0.0.1:18004")
    args = p.parse_args()
    data = asyncio.run(run(args))
    data["summary"] = {"passed": sum(r["pass"] for r in data["results"]), "cases": len(data["results"])}
    name = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in data["model"])
    base = f"rca-{name}-{datetime.now(UTC):%Y-%m-%d}"
    (ROOT / "evals/reports" / f"{base}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
    (ROOT / "evals/reports" / f"{base}.md").write_text(markdown(data))
    print(markdown(data))


if __name__ == "__main__":
    main()
