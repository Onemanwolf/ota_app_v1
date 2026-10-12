from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from opstriage_contracts.problems import ProblemEvent
from opstriage_contracts.tools import TargetSummary


class ProblemContext(BaseModel):
    """What the classifier sees: the event, the resolved target and a compact log excerpt (untrusted text)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    event: ProblemEvent
    target: TargetSummary | None = None
    recent_restarts_24h: int = 0
    logs: tuple[str, ...] = ()
