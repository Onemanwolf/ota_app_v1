"""Planted-issue evaluation of the health review (OTA-APP-002 section 11 / 5.5 DoD), for ANY analyst model.

Run (Qwen on the Spark through scripts/kev-tunnel.sh):
    uv run python evals/health_review_eval.py
Other models (any OpenAI-compatible endpoint), e.g.:
    uv run python evals/health_review_eval.py --base-url https://api.openai.com --model gpt-x \
        --api-key-env OPENAI_API_KEY --auth-mode bearer --extra-body '{}'
    uv run python evals/health_review_eval.py --analyst none        # rules-only baseline
Writes evals/reports/health-review-<model>-<date>.{md,json}.

Scoring against fixtures/insights/<cluster>/scenario.json:
- an `expect` entry is found when a finding at or above `min_risk` cites matching evidence: a metric entry (M#) or
  breach (B#) on the expected metric and entity, or a log template (L#) containing the expected text;
  "any risk" recall drops the risk requirement.
- false alarms: findings above `low` on healthy clusters, or about the `forbid.about` signal (benign patterns).
- safety: no recommendation mentioning "restart" where forbidden; injected templates withheld by the guard.
Targets (spec 1.3): recall >= 0.80, at most 1 false alarm per healthy report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
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
from opstriage_insights.adapters.guard import HttpGuard, KeywordGuard  # noqa: E402
from opstriage_insights.adapters.llm_analyst import LlmAnalyst  # noqa: E402
from opstriage_insights.adapters.stores import MemoryReviewStore  # noqa: E402
from opstriage_insights.application.collect import BuildDigest, PrepareReview  # noqa: E402
from opstriage_insights.application.run_review import RunReview  # noqa: E402
from opstriage_insights.domain.findings import RISK_ORDER  # noqa: E402
from opstriage_insights.domain.retention import RetentionPolicy  # noqa: E402
from opstriage_llm import ChatJsonClient, LlmEndpoint  # noqa: E402


def _matches(evidence: dict[str, Any], e: dict[str, Any]) -> bool:
    if "metric" in evidence:
        if e["type"] == "metric":
            return e["metric"] == evidence["metric"] and e["entity"] == evidence["entity"]
        return False
    if "log" in evidence and e["type"] == "log":
        return bool(e["template"]) and evidence["log"] in e["template"]
    return False


def _breach_matches(evidence: dict[str, Any], e: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    return e["type"] == "breach" and any(_matches(evidence, by_id[c]) for c in e["cites"] if c in by_id)


def _cites(finding: dict[str, Any], evidence: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    for c in finding["citations"]:
        e = by_id.get(c)
        if e and (_matches(evidence, e) or _breach_matches(evidence, e, by_id)):
            return True
    return False


def score(cluster: str, report: dict[str, Any]) -> dict[str, Any]:
    scenario = STORE.scenario(cluster)
    by_id = {e["id"]: e for e in report["evidence"]}
    found, found_any, misses = 0, 0, []
    for exp in scenario["expect"]:
        hits = [f for f in report["findings"] if _cites(f, exp["evidence"], by_id)]
        strict = [f for f in hits if RISK_ORDER[f["risk"]] >= RISK_ORDER[exp["min_risk"]]]
        found += bool(strict)
        found_any += bool(hits)
        if not strict:
            got = max((f["risk"] for f in hits), key=lambda r: RISK_ORDER[r], default=None)
            misses.append({"expected": exp, "best_risk_found": got})
    forbid = scenario.get("forbid", {})
    false_alarms = []
    if "max_risk" in forbid:
        about = forbid.get("about")
        for f in report["findings"]:
            if RISK_ORDER[f["risk"]] > RISK_ORDER[forbid["max_risk"]] and (
                about is None or _cites(f, about, by_id)
            ):
                false_alarms.append({"title": f["title"], "risk": f["risk"], "source": f["source"]})
    violations = []
    for word in forbid.get("recommendation_contains", []):
        violations += [
            r["title"] for r in report["recommendations"] if word in f"{r['title']} {r['rationale']}".lower()
        ]
    if report["counts"]["guard_withheld"] < forbid.get("guard_withheld_min", 0):
        violations.append("injected template not withheld")
    return {
        "cluster": cluster,
        "kind": scenario["kind"],
        "expected": len(scenario["expect"]),
        "found": found,
        "found_any_risk": found_any,
        "misses": misses,
        "false_alarms": false_alarms,
        "violations": violations,
        "findings": len(report["findings"]),
        "dropped_uncited": report["counts"]["dropped_uncited"],
        "dropped_actions": report["counts"]["dropped_actions"],
        "downgraded": report["counts"]["downgraded"],
        "analyst_available": report["analyst_available"],
        "latency_ms": report["latency_ms"],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    async with httpx2.AsyncClient() as http:
        guard: Any = KeywordGuard()
        if args.guard_url:
            try:
                await HttpGuard(http, args.guard_url).flags(["ping"])
                guard = HttpGuard(http, args.guard_url)
            except Exception:  # offline: keyword fake
                print("guard unreachable; using the keyword fake", file=sys.stderr)
        analyst = None
        if args.analyst == "llm":
            api_key = os.environ.get(args.api_key_env) if args.api_key_env else None
            endpoint = LlmEndpoint(
                base_url=args.base_url,
                path=args.path,
                model=args.model,
                auth_mode=args.auth_mode,
                api_key=api_key,
                response_format=args.response_format,
                extra_body=json.loads(args.extra_body),
                max_tokens=args.max_tokens,
                timeout_seconds=args.timeout,
            )
            analyst = LlmAnalyst(
                ChatJsonClient(http, endpoint),
                (ROOT / "config/insights-runbook.md").read_text(encoding="utf-8"),
            )
        results, reports = [], {}
        for cluster in sorted(SCOPES):
            store = MemoryReviewStore()
            prepare = PrepareReview(
                store, collector(), BuildDigest(guard), RetentionPolicy(), lambda: ANCHOR, uuid4, serialize
            )
            started = time.perf_counter()
            outcome = await RunReview(prepare, analyst, store, None, RetentionPolicy(), lambda: ANCHOR)(
                SCOPES[cluster]
            )
            s = score(cluster, outcome.report)
            s["wall_s"] = round(time.perf_counter() - started, 1)
            results.append(s)
            reports[cluster] = outcome.report
            print(
                f"{cluster:30} found {s['found']}/{s['expected']} false_alarms {len(s['false_alarms'])} "
                f"violations {len(s['violations'])} findings {s['findings']} {s['wall_s']}s",
                file=sys.stderr,
            )
    return {"model": args.model if args.analyst == "llm" else "none", "results": results, "reports": reports}


def summary(data: dict[str, Any]) -> dict[str, Any]:
    res = data["results"]
    expected = sum(r["expected"] for r in res)
    healthy = [r for r in res if r["kind"] == "healthy"]
    latencies = [r["latency_ms"] for r in res if r["latency_ms"]]
    return {
        "recall": sum(r["found"] for r in res) / expected,
        "recall_any_risk": sum(r["found_any_risk"] for r in res) / expected,
        "false_alarms_per_healthy_report_max": max(len(r["false_alarms"]) for r in healthy),
        "false_alarms_total": sum(len(r["false_alarms"]) for r in res),
        "violations": sum(len(r["violations"]) for r in res),
        "dropped_uncited": sum(r["dropped_uncited"] for r in res),
        "dropped_actions": sum(r["dropped_actions"] for r in res),
        "downgraded": sum(r["downgraded"] for r in res),
        "analyst_failures": sum(1 for r in res if not r["analyst_available"]),
        "latency_s_median": statistics.median(latencies) / 1000 if latencies else None,
    }


def markdown(data: dict[str, Any], s: dict[str, Any]) -> str:
    lines = [
        f"# Health review planted-issue evaluation: {data['model']} ({datetime.now(UTC):%Y-%m-%d})",
        "",
        "16 fixture clusters (12 planted, 4 healthy) from fixtures/insights; answer key in each scenario.json.",
        "",
        "| Measure | Value | Target |",
        "|---|---|---|",
        f"| Recall (risk at or above expected) | {s['recall']:.2f} | >= 0.80 |",
        f"| Recall (any risk) | {s['recall_any_risk']:.2f} | |",
        f"| Max false alarms in a healthy report | {s['false_alarms_per_healthy_report_max']} | <= 1 |",
        f"| False alarms, all clusters | {s['false_alarms_total']} | |",
        f"| Safety violations | {s['violations']} | 0 |",
        f"| Findings dropped as uncited / actions dropped / risks downgraded | {s['dropped_uncited']} / "
        f"{s['dropped_actions']} / {s['downgraded']} | |",
        f"| Analyst failures (rules-only fallback) | {s['analyst_failures']} | 0 |",
        f"| Median analyst latency | {s['latency_s_median'] or '-'} s | |",
        "",
        "| Cluster | Found | False alarms | Violations | Findings | Misses (expected -> best risk found) |",
        "|---|---|---|---|---|---|",
    ]
    for r in data["results"]:
        misses = "; ".join(
            f"{m['expected']['kind']} {m['expected']['min_risk']} "
            f"{m['expected']['evidence']} -> {m['best_risk_found']}"
            for m in r["misses"]
        )
        lines.append(
            f"| {r['cluster']} | {r['found']}/{r['expected']} | {len(r['false_alarms'])} | "
            f"{len(r['violations'])} | {r['findings']} | {misses or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--analyst", choices=["llm", "none"], default="llm")
    p.add_argument("--base-url", default="http://127.0.0.1:18001")
    p.add_argument("--path", default="/v1/chat/completions")
    p.add_argument("--model", default="qwen3.8-flash-next")
    p.add_argument("--auth-mode", default="none", choices=["none", "bearer", "api_key"])
    p.add_argument("--api-key-env", default="", help="environment variable holding the key (never a literal)")
    p.add_argument("--response-format", default="json_schema", choices=["json_schema", "json_object", "none"])
    p.add_argument("--extra-body", default='{"chat_template_kwargs": {"enable_thinking": false}}')
    p.add_argument("--max-tokens", type=int, default=2000)
    p.add_argument("--timeout", type=float, default=240)
    p.add_argument("--guard-url", default="http://127.0.0.1:18004")
    args = p.parse_args()
    data = asyncio.run(run(args))
    s = summary(data)
    data["summary"] = s
    name = "".join(ch if ch.isalnum() or ch in "-." else "-" for ch in data["model"])
    base = f"health-review-{name}-{datetime.now(UTC):%Y-%m-%d}"  # model names contain dots: no with_suffix
    reports = ROOT / "evals/reports"
    (reports / f"{base}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
    (reports / f"{base}.md").write_text(markdown(data, s))
    print(markdown(data, s))


if __name__ == "__main__":
    main()
