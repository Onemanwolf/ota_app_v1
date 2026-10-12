"""In-memory fakes for every Triage port, so the whole graph runs without HTTP, MCP or PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from opstriage_contracts.problems import ProblemEvent
from opstriage_contracts.tools import (
    ChannelOutcome,
    ContainerStatusResult,
    NotificationRequest,
    NotificationResult,
    RemediationResult,
    RemediationStatus,
    ResolveTargetResult,
    RestartRequest,
    TargetSummary,
    TicketRequest,
    TicketResult,
)
from opstriage_contracts.triage import TriageDecision
from opstriage_triage.adapters.mcp_tools import entities_for_resolution
from opstriage_triage.adapters.rules_decision import RulesOnlyDecisionModel
from opstriage_triage.adapters.template_summarizer import TemplateSummarizer
from opstriage_triage.bootstrap import load_policy
from opstriage_triage.domain.context import ProblemContext
from opstriage_triage.graph.state import TriageDeps
from opstriage_triage.ports import ApprovalMode, ApprovalRecord, ApprovalState, DecisionInput

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

API_TARGET = TargetSummary(
    target_id="demo-orders-api",
    description="demo",
    executor="local_docker",
    autonomy="approval_required",
    container="demo-orders-api",
    host_entity_ids=("HOST-017198AD253CBD63",),
)
TARGETS_BY_ENTITY = {
    "CONTAINER-8A1F00C2D3E4B5A6": API_TARGET,
    "CONTAINER-9B2E11D3E4F5A6B7": API_TARGET.model_copy(update={"target_id": "demo-orders-worker"}),
}


class Clock:
    def now(self) -> datetime:
        return NOW


class FakeNotifications:
    def __init__(self) -> None:
        self.sent: dict[str, NotificationRequest] = {}

    async def send(self, request: NotificationRequest) -> NotificationResult:
        duplicate = request.idempotency_key in self.sent
        self.sent.setdefault(request.idempotency_key, request)
        return NotificationResult(
            idempotency_key=request.idempotency_key,
            duplicate=duplicate,
            outcomes=tuple(ChannelOutcome(channel=c, delivered=True) for c in request.channels),
        )

    def kinds(self) -> list[str]:
        return [r.kind.value for r in self.sent.values()]


class FakeTickets:
    def __init__(self) -> None:
        self.open: dict[str, str] = {}
        self.resolved: list[str] = []

    async def create(self, request: TicketRequest) -> TicketResult:
        duplicate = request.problem_id in self.open
        tid = self.open.setdefault(request.problem_id, f"TCK-{len(self.open) + 1:06d}")
        return TicketResult(ticket_id=tid, status="open", duplicate=duplicate)

    async def resolve(self, problem_id: str, resolution: str) -> TicketResult | None:
        tid = self.open.pop(problem_id, None)
        if tid is None:
            return None
        self.resolved.append(tid)
        return TicketResult(ticket_id=tid, status="resolved")


class FakeRemediation:
    def __init__(self, status: RemediationStatus = RemediationStatus.DRY_RUN) -> None:
        self.requests: list[RestartRequest] = []
        self.status = status

    async def restart(self, request: RestartRequest) -> RemediationResult:
        self.requests.append(request)
        return RemediationResult(
            target_id=request.target_id,
            idempotency_key=request.idempotency_key,
            status=self.status,
            reason="fake",
            started_at=NOW,
            finished_at=NOW,
        )

    async def get_remediation(self, idempotency_key: str) -> RemediationResult:
        raise AssertionError("not polled in these tests")

    async def target_status(self, target_id: str) -> ContainerStatusResult:
        return ContainerStatusResult(target_id=target_id, running=True, started_at=NOW.replace(minute=5))


class FakeTargets:
    async def resolve(self, event: ProblemEvent) -> ResolveTargetResult:
        hits = {
            TARGETS_BY_ENTITY[e.id].target_id: TARGETS_BY_ENTITY[e.id]
            for e in entities_for_resolution(event)
            if e.id in TARGETS_BY_ENTITY
        }
        if len(hits) == 1:
            return ResolveTargetResult(target=next(iter(hits.values())))
        return ResolveTargetResult(target=None, ambiguous=len(hits) > 1)


class FakeContext:
    def __init__(self) -> None:
        self.problems: dict[str, ProblemEvent] = {}
        self.logs: tuple[str, ...] = ("ERROR container exited with code 137",)

    async def get_context(self, event: ProblemEvent, resolved: ResolveTargetResult) -> ProblemContext:
        self.problems.setdefault(event.problem_id, event)
        return ProblemContext(event=event, target=resolved.target, logs=self.logs)

    async def get_problem(self, problem_id: str) -> ProblemEvent | None:
        return self.problems.get(problem_id)


class FakeApprovals:
    def __init__(self, mode: ApprovalMode) -> None:
        self._mode = mode
        self.records: dict[str, ApprovalRecord] = {}

    @property
    def mode(self) -> ApprovalMode:
        return self._mode

    async def open(self, *, run_id: str, problem_id: str, target_id: str) -> ApprovalRecord:
        return self.records.setdefault(
            run_id,
            ApprovalRecord(uuid4(), run_id, problem_id, target_id, "requested", None, None, NOW, None, None),
        )

    async def get(self, run_id: str) -> ApprovalRecord | None:
        return self.records.get(run_id)

    async def decide(
        self,
        run_id: str,
        *,
        approved: bool,
        decided_by: str,
        reason: str | None,
        new_state: ApprovalState | None = None,
    ) -> ApprovalRecord | None:
        rec = self.records.get(run_id)
        if rec is None or rec.state != "requested":
            return None
        rec = replace(
            rec,
            state=new_state or ("approved" if approved else "rejected"),
            decided_by=decided_by,
            reason=reason,
            decided_at=NOW,
        )
        self.records[run_id] = rec
        return rec

    async def cancel_for_problem(self, problem_id: str, reason: str) -> list[str]:
        out = []
        for run_id, rec in self.records.items():
            if rec.problem_id == problem_id and rec.state == "requested":
                self.records[run_id] = replace(rec, state="cancelled", reason=reason, decided_at=NOW)
                out.append(run_id)
        return out

    async def expire_older_than(self, cutoff: datetime) -> list[str]:
        return []

    async def decided_not_resumed(self) -> list[ApprovalRecord]:
        return [r for r in self.records.values() if r.state != "requested" and r.resumed_at is None]

    async def mark_resumed(self, run_id: str) -> None:
        self.records[run_id] = replace(self.records[run_id], resumed_at=NOW)


class FakeRuns:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []
        self.audit: list[tuple[str, str]] = []

    async def save_run(self, summary: dict[str, Any]) -> None:
        self.saved.append(summary)

    async def append_audit(self, run_id: str, kind: str, detail: dict[str, Any]) -> None:
        self.audit.append((run_id, kind))

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return next((s for s in self.saved if s["run_id"] == run_id), None)


class FakeInbox:
    def __init__(self) -> None:
        self.superseded: list[str] = []

    async def supersede_pending(self, problem_id: str, *, except_alert_id: UUID) -> int:
        self.superseded.append(problem_id)
        return 0

    async def latest_event(self, problem_id: str) -> ProblemEvent | None:
        return None


class FailingDecisionModel:
    async def decide(self, request: DecisionInput) -> TriageDecision:
        from opstriage_triage.ports import DecisionModelError

        raise DecisionModelError("connection refused")


@dataclass
class Fakes:
    notifications: FakeNotifications = field(default_factory=FakeNotifications)
    tickets: FakeTickets = field(default_factory=FakeTickets)
    remediation: FakeRemediation = field(default_factory=FakeRemediation)
    context: FakeContext = field(default_factory=FakeContext)
    approvals: FakeApprovals = field(default_factory=lambda: FakeApprovals("auto_approve"))
    runs: FakeRuns = field(default_factory=FakeRuns)
    inbox: FakeInbox = field(default_factory=FakeInbox)
    sleeps: list[float] = field(default_factory=list)

    def deps(self, decision_model: Any = None, policy_overrides: dict[str, Any] | None = None) -> TriageDeps:
        policy = load_policy(ROOT / "config" / "policy.example.yaml")
        if policy_overrides:
            policy = policy.model_validate(policy.model_dump() | policy_overrides)

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        return TriageDeps(
            decision_model=decision_model or RulesOnlyDecisionModel(),
            context_source=self.context,
            notifications=self.notifications,
            tickets=self.tickets,
            remediation=self.remediation,
            approvals=self.approvals,
            summarizer=TemplateSummarizer(),
            policy=policy,
            targets=FakeTargets(),
            runs=self.runs,
            inbox=self.inbox,
            clock=Clock(),
            sleep=sleep,  # type: ignore[arg-type]
            run_url_base="http://localhost:8010",
            max_state_chars=12_000,
        )


def workflow_event(name: str) -> ProblemEvent:
    from opstriage_contracts.problems import AlertSource, EntityRef, ProblemStatus

    body = json.loads((ROOT / "fixtures" / "dynatrace" / "workflow" / f"{name}.json").read_text())
    ent = lambda e: EntityRef(id=e["id"], type=e["type"], name=e.get("name"))  # noqa: E731
    tags = dict(body.get("tags") or {})
    if body.get("maintenance"):
        tags["maintenance"] = "true"
    return ProblemEvent(
        source=AlertSource.SIMULATOR,
        problem_id=body["event_id"],
        display_id=body["display_id"],
        status=ProblemStatus(body["status"]),
        title=body["name"],
        category=body["category"],
        severity=body["severity"],
        started_at=body["start"],
        ended_at=body.get("end"),
        affected_entities=tuple(ent(e) for e in body["affected"]),
        root_cause_entity=ent(body["root_cause"]) if body.get("root_cause") else None,
        tags=tags,
        observed_at=NOW,
    )


@pytest.fixture
def fakes() -> Fakes:
    return Fakes()


@pytest.fixture
def event_factory() -> Callable[[str], ProblemEvent]:
    return workflow_event


@pytest.fixture
def fake_types() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        Approvals=FakeApprovals, Remediation=FakeRemediation, FailingDecisionModel=FailingDecisionModel
    )
