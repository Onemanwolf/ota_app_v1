from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from opstriage_shared.clock import Clock
from opstriage_triage.domain.policy import Policy
from opstriage_triage.ports import (
    ApprovalPort,
    DecisionModel,
    InboxPort,
    NotificationGateway,
    ProblemContextSource,
    RemediationGateway,
    RunRepository,
    Summarizer,
    TargetResolver,
    TicketingGateway,
)


class TriageState(TypedDict, total=False):
    run_id: str
    event: dict[str, Any]  # ProblemEvent.model_dump(mode="json")
    context: dict[str, Any]  # ProblemContext dump
    target: dict[str, Any] | None  # ResolveTargetResult dump; resolved deterministically, never by the model
    decision: dict[str, Any]  # TriageDecision.model_dump(mode="json")
    decision_error: str | None  # set when the decision model failed and the rules fallback decided
    plan: list[str]  # destinations chosen by the policy gate
    approval: dict[str, Any] | None
    outcomes: Annotated[list[dict[str, Any]], operator.add]  # reducer: parallel writers


class RemediationInput(TypedDict, total=False):
    run_id: str
    event: dict[str, Any]
    target: dict[str, Any] | None
    decision: dict[str, Any]


class RemediationState(RemediationInput, total=False):
    approval: dict[str, Any] | None
    remediation: dict[str, Any] | None
    verification: dict[str, Any] | None
    outcomes: Annotated[list[dict[str, Any]], operator.add]


class RemediationOutput(TypedDict, total=False):
    approval: dict[str, Any] | None
    outcomes: Annotated[list[dict[str, Any]], operator.add]


Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TriageDeps:
    """Injected through LangGraph Runtime[TriageDeps]. Built once in bootstrap.py."""

    decision_model: DecisionModel
    context_source: ProblemContextSource
    notifications: NotificationGateway
    tickets: TicketingGateway
    remediation: RemediationGateway
    approvals: ApprovalPort
    summarizer: Summarizer
    policy: Policy
    targets: TargetResolver
    runs: RunRepository
    inbox: InboxPort
    clock: Clock
    sleep: Sleep
    run_url_base: str
    max_state_chars: int
    rca_url_base: str = ""  # health-review web view (OTA-APP-002 3.4); empty = no root-cause link

    def run_url(self, run_id: str) -> str:
        return f"{self.run_url_base.rstrip('/')}/v1/runs/{run_id}"
