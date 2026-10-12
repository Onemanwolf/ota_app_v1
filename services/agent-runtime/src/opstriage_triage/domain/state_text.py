"""StateBuilder (spec 12.4.4): renders the decision model's `state` text. Logs are cut first."""

from __future__ import annotations

from opstriage_contracts.problems import Evidence
from opstriage_triage.domain.context import ProblemContext

_SEVERITY = {1: "1 = Critical", 2: "2 = High", 3: "3 = Medium", 4: "4 = Low", 5: "5 = Informational"}


def _severity(value: int | None) -> str:
    return f"{_SEVERITY[value]} (1 is most severe)" if value else "not set"


def _num(value: float | None) -> str:
    return "?" if value is None else (f"{value:g}")


def _evidence(ev: Evidence) -> str:
    parts = [ev.evidence_type, ev.display_name]
    if ev.metric_id or ev.value_after is not None:
        parts.append(f"{_num(ev.value_before)} -> {_num(ev.value_after)} {ev.unit or ''}".rstrip())
    if ev.metric_id:
        parts.append(f"[{ev.metric_id}]")
    if ev.event_type:
        parts.append(f"[{ev.event_type}]")
    if ev.entity:
        parts.append(f"on {ev.entity.name or ev.entity.id}")
    if ev.root_cause_relevant:
        parts.append("(root-cause relevant)")
    if ev.started_at and ev.ended_at is None:
        parts.append("(still active)")
    return " ".join(parts)


def build_state_text(ctx: ProblemContext, max_chars: int) -> str:
    e = ctx.event
    lines = [
        f"Problem: {e.title}",
        f"Status: {e.status.value}",
        f"Category: {e.category or 'unknown'}",
        f"Dynatrace severity: {_severity(e.severity)}",
        f"Started: {e.started_at.isoformat()}",
    ]
    if e.root_cause_entity:
        rc = e.root_cause_entity
        lines.append(f"Root cause entity: {rc.type} {rc.name or ''} ({rc.id})".replace("  ", " "))
    if e.affected_entities:
        lines.append(
            "Affected entities: " + "; ".join(f"{a.type} {a.name or a.id}" for a in e.affected_entities)
        )
    if e.impact_levels:
        lines.append("Impact level: " + ", ".join(e.impact_levels))
    if e.is_duplicate:
        lines.append("Duplicate: merged into another Dynatrace problem")
    for ev in ctx.all_evidence():
        lines.append("Evidence: " + _evidence(ev))
    if e.tags:
        lines.append("Tags: " + ", ".join(f"{k}={v}" for k, v in sorted(e.tags.items())))
    if ctx.target:
        lines.append(
            f"Restart target registered: yes ({ctx.target.target_id}, autonomy {ctx.target.autonomy})"
        )
        lines.append(f"Restarts of this target in the last 24 hours: {ctx.recent_restarts_24h}")
    else:
        lines.append("Restart target registered: no")
    head = "\n".join(lines)
    if not ctx.logs:
        return head[:max_chars]
    budget = max_chars - len(head) - len("\nRecent error and warning logs (newest first):\n")
    kept: list[str] = []
    for line in ctx.logs:
        if budget - len(line) - 1 < 0:
            break
        kept.append(line)
        budget -= len(line) + 1
    if not kept:
        return head[:max_chars]
    return head + "\nRecent error and warning logs (newest first):\n" + "\n".join(kept)
