from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from opstriage_triage.graph.build import TriageGraph
from opstriage_triage.ports import ApprovalPort, RunRepository


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    approver: str = Field(min_length=3, max_length=200)
    reason: str | None = Field(default=None, max_length=500)


def build_runs_router(
    graph: TriageGraph,
    runs: RunRepository,
    approvals: ApprovalPort,
    admin_guard: Callable[..., None],
    on_decision: Callable[[], None],
) -> APIRouter:
    router = APIRouter(prefix="/v1/runs", dependencies=[Depends(admin_guard)])

    @router.get("/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        snapshot = await graph.aget_state({"configurable": {"thread_id": run_id}})
        stored = await runs.get_run(run_id)
        approval = await approvals.get(run_id)
        if not snapshot.values and stored is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown run")
        values = snapshot.values or {}
        if stored is not None and not snapshot.next:
            state = "completed"
        elif snapshot.interrupts:
            state = "awaiting_approval"
        else:
            state = "in_progress"
        return {
            "run_id": run_id,
            "state": state,
            "next": list(snapshot.next),
            "event": values.get("event"),
            "target": values.get("target"),
            "decision": values.get("decision"),
            "decision_error": values.get("decision_error"),
            "plan": values.get("plan"),
            "approval": values.get("approval"),
            "approval_request": None
            if approval is None
            else {
                "approval_id": str(approval.approval_id),
                "state": approval.state,
                "target_id": approval.target_id,
                "decided_by": approval.decided_by,
                "reason": approval.reason,
                "requested_at": approval.requested_at.isoformat(),
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
            },
            "outcomes": values.get("outcomes", []),
            "audit": (stored or {}).get("audit", []),
        }

    @router.post("/{run_id}/approval", status_code=status.HTTP_202_ACCEPTED)
    async def decide(run_id: str, body: ApprovalDecision) -> dict[str, str]:
        record = await approvals.decide(
            run_id, approved=body.decision == "approve", decided_by=body.approver, reason=body.reason
        )
        if record is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "no pending approval for this run")
        on_decision()  # the resumer applies it in the background
        return {"run_id": run_id, "status": "resuming", "decision": record.state}

    return router
