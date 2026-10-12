"""Remediation application service. Module 1: every accepted request is recorded as a dry run.

Guard rails implemented now (spec 12.5.2 order): 1 idempotency, 2 kill switch, 3 target lookup,
4 autonomy and approval proof, 8 dry run. Steps 5-7 (serialization, cooldown and cap, circuit breaker) and
step 9 (real execution) arrive in Module 4.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta

from opstriage_contracts.tools import (
    ContainerStatusResult,
    RemediationResult,
    RemediationStatus,
    ResolveTargetRequest,
    ResolveTargetResult,
    RestartRequest,
    TargetSummary,
)
from opstriage_remediation.domain.errors import PolicyViolation
from opstriage_remediation.domain.execution import Execution
from opstriage_remediation.domain.targets import Autonomy, ExecutorKind, TargetRegistry, lower_autonomy
from opstriage_remediation.ports.executor import RestartExecutor
from opstriage_remediation.ports.store import ExecutionStore
from opstriage_shared.clock import Clock

COUNTED = (RemediationStatus.IN_PROGRESS, RemediationStatus.RESTARTED, RemediationStatus.FAILED)


@dataclass(frozen=True, slots=True)
class GuardSettings:
    enabled: bool
    dry_run: bool
    max_autonomy: Autonomy
    approval_ttl: timedelta
    allowed_approval_modes: Collection[str]


class RemediationService:
    def __init__(
        self,
        registry: TargetRegistry,
        store: ExecutionStore,
        executors: dict[ExecutorKind, RestartExecutor],
        clock: Clock,
        guard: GuardSettings,
    ) -> None:
        self._registry, self._store, self._executors = registry, store, executors
        self._clock, self._guard = clock, guard

    def list_targets(self) -> list[TargetSummary]:
        return [t.summary(self._guard.max_autonomy) for t in self._registry.targets]

    async def resolve_target(self, request: ResolveTargetRequest) -> ResolveTargetResult:
        target, ambiguous = self._registry.match(request.entities)
        if target is None:
            return ResolveTargetResult(target=None, ambiguous=ambiguous)
        recent = await self._store.count_since(target.id, self._clock.now() - timedelta(hours=24), COUNTED)
        return ResolveTargetResult(
            target=target.summary(self._guard.max_autonomy), recent_restarts_24h=recent
        )

    async def get_remediation(self, idempotency_key: str) -> RemediationResult | None:
        execution = await self._store.get(idempotency_key)
        return execution.to_result() if execution else None

    async def get_target_status(self, target_id: str) -> ContainerStatusResult:
        target = self._registry.get(target_id)
        if target is None:
            raise PolicyViolation(f"unknown target: {target_id}")
        status = await self._executors[target.executor.kind].status(target)
        return ContainerStatusResult(
            target_id=target_id, running=status.running, started_at=status.started_at, detail=status.detail
        )

    async def restart_container(self, req: RestartRequest) -> RemediationResult:
        # 1. Idempotency
        existing = await self._store.get(req.idempotency_key)
        if existing is not None:
            return existing.to_result(duplicate=True)
        # 2. Kill switch
        if not self._guard.enabled:
            return await self._reject(req, "disabled")
        # 3. Target lookup
        target = self._registry.get(req.target_id)
        if target is None:
            raise PolicyViolation(f"unknown target: {req.target_id}")
        # 4. Autonomy and approval
        autonomy = lower_autonomy(target.policy.autonomy, self._guard.max_autonomy)
        if autonomy == "notify_only":
            return await self._reject(req, "target autonomy is notify_only")
        approved_by: str | None = None
        if autonomy == "approval_required":
            problem = self._approval_problem(req)
            if problem:
                return await self._reject(req, problem)
            approved_by = req.approval.approved_by if req.approval else None
        # 8. Dry run: the server can force a dry run, never force a real run.
        now = self._clock.now()
        reason = (
            "dry run"
            if (self._guard.dry_run or req.dry_run)
            else "dry run (real execution arrives in Module 4)"
        )
        execution = Execution(
            idempotency_key=req.idempotency_key,
            target_id=target.id,
            problem_id=req.problem_id,
            status=RemediationStatus.DRY_RUN,
            reason=reason,
            executor=target.executor.kind.value,
            approved_by=approved_by,
            started_at=now,
            finished_at=now,
            output_tail=None,
        )
        if not await self._store.insert(execution):  # same key arrived concurrently
            stored = await self._store.get(req.idempotency_key)
            if stored is not None:
                return stored.to_result(duplicate=True)
        return execution.to_result()

    def _approval_problem(self, req: RestartRequest) -> str | None:
        proof = req.approval
        if proof is None:
            return "approval required"
        if proof.mode not in self._guard.allowed_approval_modes:
            return f"approval mode {proof.mode} not allowed"
        if self._clock.now() - proof.approved_at > self._guard.approval_ttl:
            return "approval expired"
        return None

    async def _reject(self, req: RestartRequest, reason: str) -> RemediationResult:
        await self._store.record_rejection(
            idempotency_key=req.idempotency_key,
            target_id=req.target_id,
            problem_id=req.problem_id,
            reason=reason,
        )
        return RemediationResult(
            target_id=req.target_id,
            idempotency_key=req.idempotency_key,
            status=RemediationStatus.REJECTED,
            reason=reason,
        )
