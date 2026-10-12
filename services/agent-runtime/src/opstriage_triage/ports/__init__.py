"""Outbound ports of the Triage context. Adapters implement these; the graph only sees these."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from opstriage_contracts.problems import ProblemEvent
from opstriage_contracts.tools import (
    ContainerStatusResult,
    NotificationRequest,
    NotificationResult,
    RemediationResult,
    ResolveTargetResult,
    RestartRequest,
    TicketRequest,
    TicketResult,
)
from opstriage_contracts.triage import TriageDecision
from opstriage_triage.domain.context import ProblemContext


class DecisionModelError(RuntimeError):
    """Timeout, transport error, 5xx or unparseable response. Triggers the rules fallback."""


class GatewayError(RuntimeError):
    """A side-effect call through MCP failed."""


@dataclass(frozen=True, slots=True)
class DecisionInput:
    context: ProblemContext
    resolved: ResolveTargetResult
    state_text: str


class DecisionModel(Protocol):
    async def decide(self, request: DecisionInput) -> TriageDecision: ...


class ProblemContextSource(Protocol):
    async def get_context(self, event: ProblemEvent, resolved: ResolveTargetResult) -> ProblemContext: ...

    async def get_problem(self, problem_id: str) -> ProblemEvent | None:
        """Re-read the problem from the source of truth (the guard's trust-but-verify step)."""
        ...


class TargetResolver(Protocol):
    async def resolve(self, event: ProblemEvent) -> ResolveTargetResult: ...


class NotificationGateway(Protocol):
    async def send(self, request: NotificationRequest) -> NotificationResult: ...


class TicketingGateway(Protocol):
    async def create(self, request: TicketRequest) -> TicketResult: ...

    async def resolve(self, problem_id: str, resolution: str) -> TicketResult | None:
        """None when no ticket is open for the problem."""
        ...


class RemediationGateway(Protocol):
    async def restart(self, request: RestartRequest) -> RemediationResult: ...

    async def get_remediation(self, idempotency_key: str) -> RemediationResult: ...

    async def target_status(self, target_id: str) -> ContainerStatusResult: ...


class Summarizer(Protocol):
    async def summarize(
        self, event: ProblemEvent, decision: dict[str, Any] | None, context: dict[str, Any] | None
    ) -> str: ...


ApprovalMode = Literal["auto_approve", "auto_reject", "manual_api"]
ApprovalState = Literal["requested", "approved", "rejected", "expired", "cancelled"]


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: UUID
    run_id: str
    problem_id: str
    target_id: str
    state: ApprovalState
    decided_by: str | None
    reason: str | None
    requested_at: datetime
    decided_at: datetime | None
    resumed_at: datetime | None


class ApprovalPort(Protocol):
    """Approval records. The decision channel (manual API in v1) writes decisions; a resumer applies them."""

    @property
    def mode(self) -> ApprovalMode: ...

    async def open(self, *, run_id: str, problem_id: str, target_id: str) -> ApprovalRecord:
        """Idempotent by run_id."""
        ...

    async def get(self, run_id: str) -> ApprovalRecord | None: ...

    async def decide(
        self,
        run_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str | None,
        new_state: ApprovalState | None = None,
    ) -> ApprovalRecord | None:
        """Conditional: only a `requested` row changes. None when it was not pending."""
        ...

    async def cancel_for_problem(self, problem_id: str, reason: str) -> list[str]:
        """Cancel pending approvals for a problem (problem CLOSED). Returns the affected run IDs."""
        ...

    async def expire_older_than(self, cutoff: datetime) -> list[str]: ...

    async def decided_not_resumed(self) -> list[ApprovalRecord]: ...

    async def mark_resumed(self, run_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ClaimedAlert:
    alert_id: UUID
    event: ProblemEvent
    attempts: int


class InboxPort(Protocol):
    async def claim_next(self) -> ClaimedAlert | None: ...

    async def wait_for_notify(self, max_wait: float) -> None: ...

    async def mark(self, alert_id: UUID, state: Literal["done", "awaiting_approval"]) -> None: ...

    async def fail(self, alert_id: UUID, error: str) -> Literal["pending", "failed"]: ...

    async def sweep_expired(self) -> list[UUID]:
        """Mark expired leases on their last attempt as failed. Returns the failed alert IDs."""
        ...

    async def supersede_pending(self, problem_id: str, *, except_alert_id: UUID) -> int: ...

    async def latest_event(self, problem_id: str) -> ProblemEvent | None: ...

    async def get_event(self, alert_id: UUID) -> ProblemEvent | None: ...


class RunRepository(Protocol):
    async def save_run(self, summary: dict[str, Any]) -> None: ...

    async def append_audit(self, run_id: str, kind: str, detail: dict[str, Any]) -> None: ...

    async def get_run(self, run_id: str) -> dict[str, Any] | None: ...
