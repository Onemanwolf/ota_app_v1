from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from opstriage_contracts.problems import Evidence, ProblemEvent
from opstriage_contracts.tools import TargetSummary


class ProblemContext(BaseModel):
    """What the classifier sees: the event, the resolved target and a compact log excerpt (untrusted text)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    event: ProblemEvent
    target: TargetSummary | None = None
    recent_restarts_24h: int = 0
    logs: tuple[str, ...] = ()
    # Evidence fetched by the context source (Grail: dt.davis.events joined via dt.davis.event_ids).
    # Workflow and Grail problem records carry none; Problems API v2 puts it on the event itself.
    evidence: tuple[Evidence, ...] = ()

    def all_evidence(self) -> tuple[Evidence, ...]:
        seen = {(e.evidence_type, e.display_name, e.metric_id, e.event_type) for e in self.event.evidence}
        extra = tuple(
            e
            for e in self.evidence
            if (e.evidence_type, e.display_name, e.metric_id, e.event_type) not in seen
        )
        return self.event.evidence + extra
