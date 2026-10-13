"""The report a review produces (OTA-APP-002 3.3 step 8, 7.1): JSON plus Markdown, expiring after 48h.

The report keeps only what the reader needs after the evidence is deleted (4h after completion): validated
findings, recommendations, and the cited digest entries, including a downsampled copy of each cited metric for
the web view's sparklines. Every log-derived string here was redacted at collection.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from opstriage_insights.domain.digest import BreachEntry, Digest, LogEntry, MetricEntry, ProblemEntry
from opstriage_insights.domain.findings import RISK_ORDER, Finding, Recommendation, Validated, worst_risk

KIND_TITLES = {
    "current_issue": "Current issues",
    "potential_issue": "Potential issues",
    "root_cause_hypothesis": "Root-cause hypotheses",
    "trend": "Trends",
}


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _dur_h(d: timedelta | None) -> float | None:
    return round(d / timedelta(hours=1), 2) if d is not None else None


def _breach(e: BreachEntry) -> dict[str, Any]:
    b = e.breach
    return {
        "id": e.cid,
        "type": "breach",
        "rule": b.rule.id,
        "level": b.rule.level,
        "entity": b.entity,
        "description": b.rule.description,
        "source": b.rule.source,
        "worst": b.worst,
        "threshold": b.rule.threshold,
        "check": b.rule.check,
        "first": _iso(b.first),
        "last": _iso(b.last),
        "active": b.active,
        "cites": list(e.cites),
    }


def _metric(e: MetricEntry) -> dict[str, Any]:
    s = e.stats
    return {
        "id": e.cid,
        "type": "metric",
        "metric": e.metric,
        "entity": e.entity,
        "stats_24h": None
        if s is None
        else {"min": s.min, "p50": s.p50, "p95": s.p95, "max": s.max, "last": s.last},
        "daily": e.daily,
        "slope_per_day": e.slope_per_day,
        "drift_30d": e.drift_30d,
        "change_1d_vs_7d": e.change_1d_vs_7d,
        "forecast": None
        if not e.forecast
        else {"threshold": e.forecast[0], "direction": e.forecast[1], "eta_hours": _dur_h(e.forecast[2])},
        "recurring_hours": list(e.recurring_hours),
        "departed_at": _iso(e.departed_at),
        "reasons": list(e.reasons),
        "sparkline": list(e.sparkline),
    }


def _log(e: LogEntry) -> dict[str, Any]:
    return {
        "id": e.cid,
        "type": "log",
        "status": e.status,
        "count": e.count,
        "recent_6h": e.recent,
        "first_seen": _iso(e.first_seen),
        "last_seen": _iso(e.last_seen),
        "logger": e.logger,
        "template": None if e.withheld else e.template,
        "examples": [] if e.withheld else list(e.examples),
        "withheld": e.withheld,
        "sources": [{"source": s, "count": n} for s, n in e.sources],
        "hourly": [{"hour": _iso(h), "count": n} for h, n in e.hourly],
    }


def _problem(e: ProblemEntry) -> dict[str, Any]:
    return {
        "id": e.cid,
        "type": "problem",
        "display_id": e.display_id,
        "title": e.title,
        "category": e.category,
        "status": e.status,
        "start": _iso(e.start),
        "duration_hours": _dur_h(e.duration),
        "entities": len(e.entities),
        "repeats": e.repeats,
    }


def _finding(f: Finding) -> dict[str, Any]:
    return {
        "kind": f.kind,
        "title": f.title,
        "detail": f.detail,
        "risk": f.risk,
        "confidence": f.confidence,
        "citations": list(f.citations),
        "eta": f.eta,
        "confirm_by": f.confirm_by,
        "source": f.source,
        "forecasts": list(f.forecasts),
        "notes": list(f.notes),
    }


def _recommendation(r: Recommendation) -> dict[str, Any]:
    return {
        "title": r.title,
        "rationale": r.rationale,
        "effort": r.effort,
        "citations": list(r.citations),
        "addresses": list(r.addresses),
    }


@dataclass(frozen=True)
class ReportMeta:
    review_id: str
    kind: str  # review | rca
    cluster: str
    generated_at: datetime
    expires_at: datetime
    model: str | None
    latency_ms: int | None
    subject: dict[str, Any] | None = None


def build(meta: ReportMeta, digest: Digest, validated: Validated) -> dict[str, Any]:
    """The stored report document. Evidence keeps only cited entries (plus all breaches)."""
    cited = {c for f in validated.findings for c in f.citations} | {
        c for r in validated.recommendations for c in r.citations
    }
    cited |= {b.cid for b in digest.breaches} | {c for b in digest.breaches for c in b.cites}
    evidence: list[dict[str, Any]] = [_breach(b) for b in digest.breaches]
    evidence += [_metric(m) for m in digest.metrics if m.cid in cited]
    evidence += [_log(e) for e in digest.logs if e.cid in cited]
    evidence += [_problem(p) for p in digest.problems if p.cid in cited]
    risk = worst_risk(validated.findings)
    counts = {
        "findings": len(validated.findings),
        "by_risk": {r: sum(1 for f in validated.findings if f.risk == r) for r in RISK_ORDER},
        "dropped_uncited": validated.dropped_uncited,
        "dropped_actions": validated.dropped_actions,
        "downgraded": validated.downgraded,
        "guard_withheld": digest.guard_withheld,
        "guard_available": digest.guard_available,
        "redactions": dict(digest.redactions),
        "digest_tokens": digest.tokens,
        "digest_trimmed": dict(digest.dropped),
    }
    return {
        "version": 1,
        "review_id": meta.review_id,
        "kind": meta.kind,
        "cluster": meta.cluster,
        "subject": meta.subject,
        "generated_at": meta.generated_at.isoformat(),
        "expires_at": meta.expires_at.isoformat(),
        "model": meta.model,
        "latency_ms": meta.latency_ms,
        "analyst_available": validated.analyst_available,
        "summary": validated.summary,
        "worst_risk": risk,
        "findings": [_finding(f) for f in validated.findings],
        "recommendations": [_recommendation(r) for r in validated.recommendations],
        "evidence": evidence,
        "counts": counts,
        "notes": list(validated.notes),
    }


def _md_cites(cites: Iterable[str]) -> str:
    return " ".join(f"[{c}](#{c.lower()})" for c in cites)


def _safeguards(c: dict[str, Any]) -> str:
    guard = "" if c["guard_available"] else " (guard unavailable: all log text withheld)"
    return (
        f"Safeguards: {c['dropped_uncited']} uncited finding(s) dropped, {c['dropped_actions']} "
        f"automated-action recommendation(s) dropped, {c['downgraded']} risk downgrade(s), "
        f"{c['guard_withheld']} log template(s) withheld by the injection guard{guard}."
    )


def _finding_lines(i: int, f: dict[str, Any]) -> list[str]:
    badge = " `rule`" if f["source"] == "rule" else ""
    out = [
        f"{i}. **{f['risk'].upper()}**{badge} · {f['title']} (confidence {f['confidence']})",
        f"   {f['detail']}",
    ]
    out += [f"   Forecast (computed): {fc}" for fc in f["forecasts"]]
    if f["eta"]:
        out.append(f"   Model estimate: {f['eta']}")
    if f["confirm_by"]:
        out.append(f"   Confirm by: {f['confirm_by']}")
    out += [f"   Note: {note}" for note in f["notes"]]
    out.append(f"   Evidence: {_md_cites(f['citations'])}")
    return out


def _evidence_line(e: dict[str, Any]) -> str:
    head = f'- <a id="{e["id"].lower()}"></a>**{e["id"]}**'
    if e["type"] == "breach":
        state = "still active" if e["active"] else "ended"
        return (
            f"{head} breach `{e['rule']}` on {e['entity']}: {e['description']} [{e['source']}], "
            f"worst {e['worst']:g}, {state}"
        )
    if e["type"] == "metric":
        s = e["stats_24h"] or {}
        fc = e["forecast"]
        extra = ""
        if fc:
            extra = f", forecast {fc['direction']} {fc['threshold']:g} in {fc['eta_hours'] / 24:.1f} d"
        return (
            f"{head} `{e['metric']}` on {e['entity']}: 24h min {s.get('min')} p50 {s.get('p50')} "
            f"max {s.get('max')} last {s.get('last')}{extra}"
        )
    if e["type"] == "log":
        text = "withheld by the injection guard" if e["withheld"] else f"`{e['template']}`"
        return f"{head} {e['status']} x{e['count']} ({e['logger'] or '-'}): {text}"
    return f"{head} {e['display_id']} {e['title']} ({e['status']})"


def markdown(report: dict[str, Any]) -> str:
    what = "root-cause analysis" if report["kind"] == "rca" else "review"
    summary_title = (
        "## Summary (AI-generated, check the cited evidence)" if report["analyst_available"] else "## Summary"
    )
    lines = [
        f"# Kafka health {what}: {report['cluster']}",
        "",
        f"Generated {report['generated_at']} · expires {report['expires_at']} · "
        f"model {report['model'] or 'none'}",
        "",
        _safeguards(report["counts"]),
        "",
        summary_title,
        "",
        report["summary"] or "-",
    ]
    for kind, title in KIND_TITLES.items():
        group = [f for f in report["findings"] if f["kind"] == kind]
        if group:
            lines += ["", f"## {title}", ""]
            for i, f in enumerate(group, 1):
                lines += _finding_lines(i, f)
    if report["recommendations"]:
        lines += ["", "## Recommendations (for people to evaluate)", ""]
        lines += [
            f"- **{r['title']}** (effort {r['effort']}): {r['rationale']} {_md_cites(r['citations'])}"
            for r in report["recommendations"]
        ]
    lines += ["", "## Evidence", ""]
    lines += [_evidence_line(e) for e in report["evidence"]]
    return "\n".join(lines) + "\n"
