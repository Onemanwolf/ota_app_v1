"""Findings and the validator that decides which ones reach a report (OTA-APP-002 6.3, D3, D4).

Model output is untrusted. A finding survives only if every citation it gives exists in the digest; `critical`
needs a deterministic breach (B#) or an active Davis problem (P#) behind it, otherwise it is downgraded
to `high`.
Recommendations that propose automated or unreviewed actions are dropped: this feature is advisory only.
Every deterministic breach becomes a rule finding, unless a model finding already cites that breach.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Literal

from opstriage_insights.domain.digest import Digest, MetricEntry

Kind = Literal["trend", "potential_issue", "current_issue", "root_cause_hypothesis"]
Risk = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
Effort = Literal["low", "medium", "high"]
RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}
KIND_ORDER: dict[str, int] = {
    "current_issue": 0,
    "potential_issue": 1,
    "root_cause_hypothesis": 2,
    "trend": 3,
}

_AUTOMATED_ACTION = re.compile(
    r"\b(auto(matically|mated)?[- ]?(restart|scale|delete|purge|remediat\w*|apply)|"
    r"restart (all|every|each|the) (broker|node|pod|container)s?|"
    r"(delete|purge|drop|truncate) (the )?(topic|partition|log|segment|data)\w*|"
    r"without (review|approval|a change)|immediately (restart|delete|apply))",
    re.IGNORECASE,
)
_CITATION = re.compile(r"^[BMLP]\d{1,4}$")


@dataclass(frozen=True, slots=True)
class Finding:
    kind: Kind
    title: str
    detail: str
    risk: Risk
    confidence: Confidence
    citations: tuple[str, ...]
    eta: str | None = None  # model's wording; the code forecast is shown next to it
    confirm_by: str | None = None
    source: Literal["model", "rule"] = "model"
    forecasts: tuple[str, ...] = ()  # code-computed, from cited metric entries
    notes: tuple[str, ...] = ()  # e.g. "risk lowered from critical: no breach or active problem cited"


@dataclass(frozen=True, slots=True)
class Recommendation:
    title: str
    rationale: str
    effort: Effort
    citations: tuple[str, ...]
    addresses: tuple[int, ...] = ()  # indexes into the model's findings list


@dataclass(frozen=True, slots=True)
class AnalystOutput:
    summary: str
    findings: tuple[Finding, ...]
    recommendations: tuple[Recommendation, ...]


@dataclass(frozen=True)
class Validated:
    summary: str
    findings: tuple[Finding, ...]
    recommendations: tuple[Recommendation, ...]
    dropped_uncited: int
    dropped_actions: int
    downgraded: int
    analyst_available: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


def _forecast_text(m: MetricEntry) -> str | None:
    if not m.forecast:
        return None
    threshold, direction, eta = m.forecast
    days = eta / timedelta(days=1)
    when = (
        "now"
        if eta == timedelta(0)
        else f"in about {days:.1f} days"
        if days >= 1
        else f"in about {days * 24:.0f} hours"
    )
    return f"{m.metric} on {m.entity} goes {direction} {threshold:g} {when} at the current rate ({m.cid})"


def rule_findings(digest: Digest) -> list[Finding]:
    """One finding per deterministic breach (D4): these never depend on the model."""
    out = []
    for be in digest.breaches:
        b = be.breach
        state = "still active" if b.active else "ended"
        out.append(
            Finding(
                kind="current_issue" if b.active else "trend",
                title=f"{b.rule.description} on {b.entity}",
                detail=(
                    f"Code check {b.rule.id} [{b.rule.source}]: worst value {b.worst:g} "
                    f"(threshold {b.rule.check} {b.rule.threshold:g}), {state}."
                ),
                risk="critical" if b.rule.level == "critical" else "medium",
                confidence="high",
                citations=(be.cid, *be.cites),
                source="rule",
            )
        )
    return out


def validate(output: AnalystOutput, digest: Digest) -> Validated:
    known = digest.citation_ids()
    metrics = {m.cid: m for m in digest.metrics}
    active_problems = {p.cid for p in digest.problems if p.status == "ACTIVE"}
    breach_ids = {b.cid for b in digest.breaches}
    kept: list[Finding] = []
    kept_index: dict[int, int] = {}
    dropped = downgraded = 0
    for i, f in enumerate(output.findings):
        cites = tuple(dict.fromkeys(c.strip().upper() for c in f.citations))
        if not cites or not all(_CITATION.match(c) and c in known for c in cites):
            dropped += 1
            continue
        notes: list[str] = []
        risk = f.risk
        if risk == "critical" and not (set(cites) & (breach_ids | active_problems)):
            risk = "high"
            downgraded += 1
            notes.append("risk lowered from critical: no breach or active problem cited")
        forecasts = tuple(t for t in (_forecast_text(metrics[c]) for c in cites if c in metrics) if t)
        kept_index[i] = len(kept)
        kept.append(
            replace(f, citations=cites, risk=risk, source="model", forecasts=forecasts, notes=tuple(notes))
        )
    cited_breaches = {c for f in kept for c in f.citations if c in breach_ids}
    rules = [r for r in rule_findings(digest) if r.citations[0] not in cited_breaches]
    findings = tuple(
        sorted([*kept, *rules], key=lambda f: (-RISK_ORDER[f.risk], KIND_ORDER[f.kind], f.title))
    )
    recs: list[Recommendation] = []
    dropped_actions = 0
    for r in output.recommendations:
        cites = tuple(dict.fromkeys(c.strip().upper() for c in r.citations))
        if _AUTOMATED_ACTION.search(f"{r.title} {r.rationale}"):
            dropped_actions += 1
            continue
        if not cites or not all(c in known for c in cites):
            dropped += 1
            continue
        addresses = tuple(kept_index[a] for a in r.addresses if a in kept_index)
        recs.append(replace(r, citations=cites, addresses=addresses))
    return Validated(output.summary.strip(), findings, tuple(recs), dropped, dropped_actions, downgraded)


def rules_only(digest: Digest, reason: str) -> Validated:
    """The report when the analyst is unavailable or keeps returning invalid output: rule findings only."""
    return Validated(
        summary="Automated analysis was unavailable; only code-checked findings are listed.",
        findings=tuple(sorted(rule_findings(digest), key=lambda f: (-RISK_ORDER[f.risk], f.title))),
        recommendations=(),
        dropped_uncited=0,
        dropped_actions=0,
        downgraded=0,
        analyst_available=False,
        notes=(reason,),
    )


def worst_risk(findings: Sequence[Finding]) -> Risk | None:
    return max((f.risk for f in findings), key=lambda r: RISK_ORDER[r], default=None)
