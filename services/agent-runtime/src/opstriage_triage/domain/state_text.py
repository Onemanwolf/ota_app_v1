"""StateBuilder (spec 12.4.4): renders the decision model's `state` text. Logs are cut first."""

from __future__ import annotations

from opstriage_triage.domain.context import ProblemContext


def build_state_text(ctx: ProblemContext, max_chars: int) -> str:
    e = ctx.event
    lines = [
        f"Problem: {e.title}",
        f"Status: {e.status.value}",
        f"Category: {e.category or 'unknown'}",
        f"Dynatrace severity (1-5): {e.severity if e.severity is not None else 'unknown'}",
        f"Started: {e.started_at.isoformat()}",
    ]
    if e.root_cause_entity:
        rc = e.root_cause_entity
        lines.append(f"Root cause entity: {rc.type} {rc.name or ''} ({rc.id})".replace("  ", " "))
    if e.affected_entities:
        lines.append(
            "Affected entities: " + "; ".join(f"{a.type} {a.name or a.id}" for a in e.affected_entities)
        )
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
