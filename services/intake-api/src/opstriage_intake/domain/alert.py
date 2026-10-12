from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AlertState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class AlertView:
    alert_id: UUID
    problem_id: str
    display_id: str | None
    status: str
    source: str
    state: AlertState
    attempts: int
    received_at: datetime
    last_error: str | None
