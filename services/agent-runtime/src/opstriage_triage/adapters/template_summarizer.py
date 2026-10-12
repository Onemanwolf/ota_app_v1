from __future__ import annotations

from typing import Any

from opstriage_contracts.problems import ProblemEvent


class TemplateSummarizer:
    """Deterministic summaries. Never influences routing."""

    async def summarize(
        self, event: ProblemEvent, decision: dict[str, Any] | None, context: dict[str, Any] | None
    ) -> str:
        root = event.root_cause_entity
        parts = [f'Dynatrace reports {event.category or "a"} problem "{event.title}" ({event.status.value}).']
        if root:
            parts.append(f"Root cause: {root.type} {root.name or root.id}.")
        if event.affected_entities:
            parts.append(f"{len(event.affected_entities)} affected entities.")
        if decision:
            parts.append(f"Triage decision: {decision['action']} (source: {decision['source']}).")
        logs = (context or {}).get("logs") or []
        if logs:
            parts.append(f"{len(logs)} recent error or warning log lines are attached to the run.")
        return " ".join(parts)[:4000]
