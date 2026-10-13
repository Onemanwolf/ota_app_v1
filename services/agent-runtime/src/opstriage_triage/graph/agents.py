"""Action agents: one node (or subgraph) per kind of action. Idempotency keys derive from run_id."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from opstriage_contracts.problems import ProblemEvent, ProblemStatus
from opstriage_contracts.tools import (
    ApprovalProof,
    NotificationKind,
    NotificationRequest,
    RemediationResult,
    RemediationStatus,
    RestartRequest,
    Severity,
    TicketRequest,
)
from opstriage_triage.graph.state import (
    RemediationInput,
    RemediationOutput,
    RemediationState,
    TriageDeps,
    TriageState,
)
from opstriage_triage.ports import GatewayError

log = structlog.get_logger()

TERMINAL = {
    RemediationStatus.DRY_RUN,
    RemediationStatus.RESTARTED,
    RemediationStatus.REJECTED,
    RemediationStatus.FAILED,
}


def idem(state: TriageState | RemediationState, step: str) -> str:
    return f"{state['run_id']}:{step}"


def _event(state: TriageState | RemediationState) -> ProblemEvent:
    return ProblemEvent.model_validate(state["event"])


def _title(event: ProblemEvent, prefix: str = "") -> str:
    return f"{prefix}[{event.display_id or event.problem_id}] {event.title}"[:200]


# ---------------------------------------------------------------------------------------------- notify
async def notify_agent(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    deps = runtime.context
    event = _event(state)
    summary = await deps.summarizer.summarize(event, state.get("decision"), state.get("context"))
    request = NotificationRequest(
        idempotency_key=idem(state, "notify:opened"),
        kind=NotificationKind.PROBLEM_OPENED,
        severity=deps.policy.notification_severity(event, state.get("decision")),
        title=_title(event),
        summary=summary,
        facts=deps.policy.notification_facts(event, state.get("decision"), state.get("target")),
        links=deps.policy.notification_links(event, deps.run_url(state["run_id"]), deps.rca_url_base),
    )
    result = await deps.notifications.send(request)
    return {
        "outcomes": [
            {
                "step": "notify",
                "ok": all(o.delivered for o in result.outcomes),
                "channels": {o.channel: o.delivered for o in result.outcomes},
            }
        ]
    }


# ---------------------------------------------------------------------------------------------- ticket
async def ticket_agent(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    deps = runtime.context
    event = _event(state)
    score = (state.get("decision") or {}).get("severity_score") or 0.0
    priority = "P1" if score >= 2.5 else "P2" if score >= 1.5 else "P3"
    summary = await deps.summarizer.summarize(event, state.get("decision"), state.get("context"))
    result = await deps.tickets.create(
        TicketRequest(
            idempotency_key=idem(state, "ticket"),
            problem_id=event.problem_id,
            title=_title(event),
            description=f"{summary}\n\nTriage run: {deps.run_url(state['run_id'])}"[:8000],
            priority=priority,
        )
    )
    return {
        "outcomes": [
            {"step": "ticket", "ok": True, "ticket_id": result.ticket_id, "duplicate": result.duplicate}
        ]
    }


# ---------------------------------------------------------------------------------------------- escalate
def _escalation_reason(state: TriageState) -> str:
    decision = state.get("decision") or {}
    target = state.get("target") or {}
    if target.get("ambiguous"):
        return "Several restart targets match this problem; a person must choose."
    if decision.get("action") in ("escalate_human", "cannot_tell"):
        return f"The classifier chose {decision['action']}."
    if decision.get("action") == "restart_container":
        return "A restart was proposed but the decision did not pass the policy thresholds."
    return "Severity is high."


async def escalation_agent(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    deps = runtime.context
    event = _event(state)
    result = await deps.notifications.send(
        NotificationRequest(
            idempotency_key=idem(state, "notify:escalation"),
            kind=NotificationKind.ESCALATION,
            severity=Severity.CRITICAL,
            title=_title(event, "ESCALATION "),
            summary=_escalation_reason(state),
            facts=deps.policy.notification_facts(event, state.get("decision"), state.get("target")),
            links=deps.policy.notification_links(event, deps.run_url(state["run_id"]), deps.rca_url_base),
        )
    )
    return {"outcomes": [{"step": "escalate", "ok": all(o.delivered for o in result.outcomes)}]}


# ---------------------------------------------------------------------------------------------- resolve
async def resolution_agent(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    """CLOSED problems: notify, resolve the ticket, cancel approvals, supersede earlier pending alerts."""
    deps = runtime.context
    event = _event(state)
    sent = await deps.notifications.send(
        NotificationRequest(
            idempotency_key=idem(state, "notify:resolved"),
            kind=NotificationKind.PROBLEM_RESOLVED,
            severity=Severity.INFO,
            title=_title(event, "RESOLVED "),
            summary=f"Dynatrace closed problem {event.display_id or event.problem_id}.",
            facts=deps.policy.notification_facts(event, None, None),
            links=deps.policy.notification_links(event, deps.run_url(state["run_id"]), deps.rca_url_base),
        )
    )
    ticket = await deps.tickets.resolve(event.problem_id, "Dynatrace closed the problem")
    cancelled = await deps.approvals.cancel_for_problem(event.problem_id, "problem_closed")
    superseded = await deps.inbox.supersede_pending(event.problem_id, except_alert_id=UUID(state["run_id"]))
    return {
        "outcomes": [
            {
                "step": "resolve",
                "ok": all(o.delivered for o in sent.outcomes),
                "ticket_id": ticket.ticket_id if ticket else None,
                "approvals_cancelled": cancelled,
                "alerts_superseded": superseded,
            }
        ]
    }


# ---------------------------------------------------------------------------------------------- finalize
async def finalize(state: TriageState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    """Deferred: runs once after every selected branch. Writes the run summary and the audit log."""
    deps = runtime.context
    event = state["event"]
    summary = {
        "run_id": state["run_id"],
        "problem_id": event["problem_id"],
        "display_id": event.get("display_id"),
        "event_status": event["status"],
        "decision": state.get("decision"),
        "decision_error": state.get("decision_error"),
        "target": state.get("target"),
        "plan": state.get("plan") or [],
        "approval": state.get("approval"),
        "outcomes": state.get("outcomes") or [],
    }
    await deps.runs.save_run(summary)
    if state.get("decision"):
        await deps.runs.append_audit(
            state["run_id"], "decision", {"decision": state["decision"], "error": state.get("decision_error")}
        )
    for outcome in state.get("outcomes") or []:
        await deps.runs.append_audit(state["run_id"], f"outcome:{outcome.get('step')}", outcome)
    return {}


# ---------------------------------------------------------------------------------------------- remediate
async def guard(state: RemediationState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    """Trust but verify (spec 12.6.4): re-read the problem and re-check the target before anything else."""
    deps = runtime.context
    event = _event(state)
    target = (state.get("target") or {}).get("target")
    if not target or (state.get("target") or {}).get("ambiguous"):
        return {"remediation": {"status": "skipped", "reason": "no unambiguous restart target"}}
    if target["autonomy"] == "notify_only":
        return {"remediation": {"status": "skipped", "reason": "target autonomy is notify_only"}}
    current = await deps.context_source.get_problem(event.problem_id)
    if current is None or current.status is not ProblemStatus.ACTIVE:
        return {"remediation": {"status": "skipped", "reason": "problem is missing or no longer ACTIVE"}}
    return {}


def after_guard(state: RemediationState) -> str:
    return "report" if state.get("remediation") else "request_approval"


async def request_approval(state: RemediationState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    """Code before interrupt() re-runs on every resume, so everything here is idempotent."""
    deps = runtime.context
    target = state["target"]["target"]  # type: ignore[index]
    if target["autonomy"] == "autonomous":
        return {"approval": {"approved": True, "mode": "not_required"}}
    mode = deps.approvals.mode
    if mode in ("auto_approve", "auto_reject"):
        approved = mode == "auto_approve"
        return {
            "approval": {
                "approved": approved,
                "approval_id": idem(state, "approval"),
                "mode": mode,
                "approved_by": "system:auto",
                "approved_at": deps.clock.now().isoformat(),
                "reason": None if approved else "auto_reject mode",
            }
        }
    event = _event(state)
    record = await deps.approvals.open(
        run_id=state["run_id"], problem_id=event.problem_id, target_id=target["target_id"]
    )
    await deps.notifications.send(
        NotificationRequest(
            idempotency_key=idem(state, "notify:proposed"),
            kind=NotificationKind.ACTION_PROPOSED,
            severity=Severity.WARNING,
            title=_title(event, "APPROVAL NEEDED "),
            summary=(
                f"Proposed action: restart {target['target_id']}. Approve or reject with "
                f"POST {deps.run_url(state['run_id'])}/approval."
            ),
            facts=deps.policy.notification_facts(event, state.get("decision"), state.get("target")),
            links=deps.policy.notification_links(event, deps.run_url(state["run_id"]), deps.rca_url_base),
        )
    )
    decision = interrupt(
        {
            "type": "approval",
            "approval_id": str(record.approval_id),
            "target_id": target["target_id"],
            "run_id": state["run_id"],
        }
    )
    return {"approval": dict(decision)}


def after_approval(state: RemediationState) -> str:
    return "execute" if (state.get("approval") or {}).get("approved") else "report"


async def execute(state: RemediationState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    deps = runtime.context
    event = _event(state)
    target = state["target"]["target"]  # type: ignore[index]
    approval = state.get("approval") or {}
    proof = None
    if approval.get("mode") in ("auto_approve", "manual_api"):
        proof = ApprovalProof(
            approval_id=str(approval["approval_id"]),
            approved_by=str(approval["approved_by"]),
            approved_at=approval["approved_at"],
            mode=approval["mode"],
        )
    request = RestartRequest(
        target_id=target["target_id"],
        idempotency_key=idem(state, "restart"),
        problem_id=event.problem_id,
        reason=f"Triage run {state['run_id']}: {event.title}"[:500],
        approval=proof,
        dry_run=deps.policy.remediation.request_dry_run,
    )
    try:
        result = await deps.remediation.restart(request)
    except GatewayError as exc:  # the tool refused (policy violation); transport errors propagate for retry
        return {"remediation": {"status": "error", "reason": str(exc)[:500]}}
    waited = 0.0
    delay = 2.0
    while (
        result.status is RemediationStatus.IN_PROGRESS
        and waited < deps.policy.remediation.execute_poll_max_seconds
    ):
        await deps.sleep(delay)
        waited += delay
        delay = min(delay * 2, 30.0)
        result = await deps.remediation.get_remediation(request.idempotency_key)
    remediation = result.model_dump(mode="json")
    if result.status not in TERMINAL:
        remediation = {**remediation, "status": "unknown", "reason": "outcome unknown after polling"}
    return {"remediation": remediation}


async def verify(state: RemediationState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    deps = runtime.context
    remediation = state.get("remediation") or {}
    if remediation.get("status") != RemediationStatus.RESTARTED:
        return {"verification": None}
    await deps.sleep(deps.policy.remediation.verify_delay_seconds)
    status = await deps.remediation.target_status(remediation["target_id"])
    before = RemediationResult.model_validate(remediation).started_at
    newer = bool(status.started_at and before and status.started_at > before)
    return {
        "verification": {
            "running": status.running,
            "restarted_after_request": newer,
            "started_at": status.started_at.isoformat() if status.started_at else None,
        }
    }


def _result_text(state: RemediationState) -> tuple[str, bool]:
    """(summary, needs_escalation)"""
    rem = state.get("remediation") or {}
    approval = state.get("approval") or {}
    status = rem.get("status")
    if status is None:
        reason = approval.get("reason") or "rejected"
        return f"Restart not executed: approval {reason}.", False
    if status == "skipped":
        return f"Restart skipped: {rem.get('reason')}.", False
    if status == RemediationStatus.DRY_RUN:
        return f"Dry run: {rem.get('target_id')} would have been restarted ({rem.get('reason')}).", False
    if status == RemediationStatus.RESTARTED:
        verification = state.get("verification") or {}
        ok = verification.get("running") and verification.get("restarted_after_request")
        return (
            f"Restarted {rem.get('target_id')}. Verification: "
            f"{'container running with a newer start time' if ok else 'NOT confirmed'}.",
            not ok,
        )
    if status == RemediationStatus.REJECTED:
        return f"Restart rejected by the remediation server: {rem.get('reason')}.", False
    return f"Restart {status}: {rem.get('reason')}. A person must check the target.", True


async def report(state: RemediationState, runtime: Runtime[TriageDeps]) -> dict[str, Any]:
    deps = runtime.context
    event = _event(state)
    summary, escalate = _result_text(state)
    await deps.notifications.send(
        NotificationRequest(
            idempotency_key=idem(state, "notify:result"),
            kind=NotificationKind.ESCALATION if escalate else NotificationKind.ACTION_RESULT,
            severity=Severity.CRITICAL if escalate else Severity.INFO,
            title=_title(event, "REMEDIATION "),
            summary=summary,
            facts=deps.policy.notification_facts(event, state.get("decision"), state.get("target")),
            links=deps.policy.notification_links(event, deps.run_url(state["run_id"]), deps.rca_url_base),
        )
    )
    rem = state.get("remediation") or {}
    outcome = {
        "step": "remediate",
        "status": rem.get("status") or "not_approved",
        "reason": rem.get("reason"),
        "approved": (state.get("approval") or {}).get("approved"),
        "escalated": escalate,
        "verification": state.get("verification"),
    }
    return {"outcomes": [outcome]}


def build_remediation_subgraph() -> CompiledStateGraph[Any, Any, Any, Any]:
    g = StateGraph(
        RemediationState,
        context_schema=TriageDeps,
        input_schema=RemediationInput,
        output_schema=RemediationOutput,
    )
    g.add_node("guard", guard)
    g.add_node("request_approval", request_approval)
    g.add_node("execute", execute)
    g.add_node("verify", verify)
    g.add_node("report", report)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", after_guard, ["request_approval", "report"])
    g.add_conditional_edges("request_approval", after_approval, ["execute", "report"])
    g.add_edge("execute", "verify")
    g.add_edge("verify", "report")
    g.add_edge("report", END)
    return g.compile()
