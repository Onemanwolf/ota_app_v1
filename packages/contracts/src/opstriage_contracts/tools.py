from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from opstriage_contracts.problems import EntityRef

TARGET_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,62}$"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ----- Notification -----
class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NotificationKind(StrEnum):
    PROBLEM_OPENED = "problem_opened"
    ACTION_PROPOSED = "action_proposed"
    ACTION_RESULT = "action_result"
    PROBLEM_RESOLVED = "problem_resolved"
    ESCALATION = "escalation"


class NotificationRequest(_Contract):
    idempotency_key: str = Field(min_length=8, max_length=200)
    kind: NotificationKind
    severity: Severity
    title: str = Field(max_length=200)
    summary: str = Field(max_length=4000)
    facts: dict[str, str] = Field(default_factory=dict)  # rendered as a FactSet / table
    links: dict[str, str] = Field(default_factory=dict)  # label -> URL (Dynatrace problem, run status)
    channels: tuple[Literal["teams", "email"], ...] = ("teams", "email")
    email_recipients: tuple[str, ...] = ()  # empty = configured defaults


class ChannelOutcome(_Contract):
    channel: Literal["teams", "email"]
    delivered: bool
    detail: str | None = None


class NotificationResult(_Contract):
    idempotency_key: str
    outcomes: tuple[ChannelOutcome, ...]
    duplicate: bool = False


# ----- Ticketing -----
class TicketRequest(_Contract):
    idempotency_key: str = Field(min_length=8, max_length=200)
    problem_id: str
    title: str = Field(max_length=200)
    description: str = Field(max_length=8000)
    priority: Literal["P1", "P2", "P3", "P4"] = "P3"


class TicketResult(_Contract):
    ticket_id: str
    url: str | None = None
    status: Literal["open", "resolved"]
    duplicate: bool = False


# ----- Remediation -----
class ApprovalProof(_Contract):
    approval_id: str
    approved_by: str
    approved_at: datetime
    mode: Literal["auto_approve", "manual_api", "teams_link", "teams_bot"]


class RestartRequest(_Contract):
    target_id: str = Field(pattern=TARGET_ID_PATTERN)
    idempotency_key: str = Field(min_length=8, max_length=200)
    problem_id: str
    reason: str = Field(max_length=500)
    approval: ApprovalProof | None = None
    dry_run: bool = True  # the server ALSO enforces its own dry-run setting


class RemediationStatus(StrEnum):
    IN_PROGRESS = "in_progress"  # executor still running; poll get_remediation
    DRY_RUN = "dry_run"
    RESTARTED = "restarted"
    REJECTED = "rejected"  # policy, cooldown, cap, kill switch, missing approval
    FAILED = "failed"


class RemediationResult(_Contract):
    target_id: str
    idempotency_key: str
    status: RemediationStatus
    reason: str | None = None
    executor: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output_tail: str | None = Field(default=None, max_length=4096)  # Run Command keeps the last 4 KB
    duplicate: bool = False


class TargetSummary(_Contract):
    target_id: str
    description: str
    executor: str
    autonomy: Literal["notify_only", "approval_required", "autonomous"]  # effective (after global ceiling)
    container: str  # used by the Grail log query in load_context
    host_entity_ids: tuple[str, ...] = ()


class ResolveTargetRequest(_Contract):
    problem_id: str
    entities: tuple[EntityRef, ...]  # root cause first, then affected entities


class ResolveTargetResult(_Contract):
    target: TargetSummary | None
    ambiguous: bool = False
    recent_restarts_24h: int = 0


class ContainerStatusResult(_Contract):
    target_id: str
    running: bool
    started_at: datetime | None = None
    detail: str | None = None
