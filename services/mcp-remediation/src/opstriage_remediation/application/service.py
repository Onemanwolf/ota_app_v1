"""Remediation application service: server-side guard rails (spec 12.5.2), executed in this order.

1 idempotency, 2 kill switch, 3 target lookup, 4 autonomy and approval proof, 5 per-target serialization,
6 cooldown and daily cap, 7 circuit breaker, 8 dry run, 9 execute in a background task owned by the server.
Rejections go to `rejections` (so a later retry with the same key can succeed); executed and dry-run
attempts go to `executions`. These rules hold whatever the caller (the triage agent or any token holder) asks.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Collection
from dataclasses import dataclass, replace
from datetime import timedelta

import structlog

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
from opstriage_remediation.domain.targets import (
    Autonomy,
    ExecutorKind,
    RestartTarget,
    TargetRegistry,
    lower_autonomy,
)
from opstriage_remediation.ports.executor import ExecutorUnavailable, RestartExecutor
from opstriage_remediation.ports.store import ExecutionStore
from opstriage_shared.clock import Clock

log = structlog.get_logger()

# Cooldown and the daily cap count these; dry runs do not (spec 10.3).
COUNTED = (RemediationStatus.IN_PROGRESS, RemediationStatus.RESTARTED, RemediationStatus.FAILED)
OUTPUT_TAIL = 4096
STALE_GRACE = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class GuardSettings:
    enabled: bool
    dry_run: bool
    max_autonomy: Autonomy
    approval_ttl: timedelta
    allowed_approval_modes: Collection[str]
    sync_wait_seconds: float = 120
    breaker_failures: int = 3
    breaker_window: timedelta = timedelta(minutes=30)


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
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------------------------------ queries
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
        target = self._target(target_id)
        try:
            status = await self._executors[target.executor.kind].status(target)
        except ExecutorUnavailable as exc:
            return ContainerStatusResult(target_id=target_id, running=False, detail=f"unavailable: {exc}")
        return ContainerStatusResult(
            target_id=target_id, running=status.running, started_at=status.started_at, detail=status.detail
        )

    # ------------------------------------------------------------------------------------------ restart
    async def restart_container(self, req: RestartRequest) -> RemediationResult:
        # 1. Idempotency: the same key never executes twice.
        existing = await self._store.get(req.idempotency_key)
        if existing is not None:
            return existing.to_result(duplicate=True)
        # 2. Kill switch.
        if not self._guard.enabled:
            return await self._reject(req, "disabled")
        # 3. Target lookup: only registry targets exist.
        target = self._target(req.target_id)
        # 4. Autonomy (the lower of target and global ceiling) and approval proof.
        autonomy = lower_autonomy(target.policy.autonomy, self._guard.max_autonomy)
        if autonomy == "notify_only":
            return await self._reject(req, "target autonomy is notify_only")
        approved_by: str | None = None
        if autonomy == "approval_required":
            problem = self._approval_problem(req)
            if problem:
                return await self._reject(req, problem)
            approved_by = req.approval.approved_by if req.approval else None
        # 6. Cooldown and daily cap (counted: in_progress, restarted, failed).
        now = self._clock.now()
        if target.policy.cooldown_minutes and await self._store.count_since(
            target.id, now - timedelta(minutes=target.policy.cooldown_minutes), COUNTED
        ):
            return await self._reject(req, f"cooldown ({target.policy.cooldown_minutes} min)")
        if await self._store.count_since(target.id, now - timedelta(hours=24), COUNTED) >= (
            target.policy.max_restarts_per_day
        ):
            return await self._reject(req, f"daily cap ({target.policy.max_restarts_per_day} per 24 h)")
        # 7. Circuit breaker: too many failures (any target) since the window start or the last reset.
        if await self._breaker_open():
            return await self._reject(
                req, "circuit breaker open (reset with: opstriage-remediation reset-breaker)"
            )

        execution = Execution(
            idempotency_key=req.idempotency_key,
            target_id=target.id,
            problem_id=req.problem_id,
            status=RemediationStatus.IN_PROGRESS,
            reason=None,
            executor=target.executor.kind.value,
            approved_by=approved_by,
            started_at=now,
            finished_at=None,
            output_tail=None,
        )
        # 8. Dry run: the server setting OR the request. The server can force a dry run, never a real run.
        if self._guard.dry_run or req.dry_run:
            dry = replace(
                execution,
                status=RemediationStatus.DRY_RUN,
                finished_at=now,
                reason="dry run (server setting)" if self._guard.dry_run else "dry run (requested)",
            )
            if not await self._store.insert(dry):
                return await self._stored_duplicate(req)
            return dry.to_result()
        # 5. Per-target serialization: inserting the IN_PROGRESS row is the lock.
        started = await self._store.start(execution)
        if started == "duplicate":
            return await self._stored_duplicate(req)
        if started == "busy":
            return await self._reject(req, "busy: another restart of this target is in progress")
        # 9. Execute in a background task owned by the server; wait up to sync_wait_seconds.
        task = asyncio.create_task(self._execute(target, execution), name=f"restart:{req.idempotency_key}")
        self._tasks[req.idempotency_key] = task
        task.add_done_callback(lambda _: self._tasks.pop(req.idempotency_key, None))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), timeout=self._guard.sync_wait_seconds)
        stored = await self._store.get(req.idempotency_key)
        return (stored or execution).to_result()

    async def _execute(self, target: RestartTarget, execution: Execution) -> None:
        executor = self._executors[target.executor.kind]
        timeout = target.policy.restart_timeout_seconds
        log.info(
            "restart_executing",
            target_id=target.id,
            executor=executor.kind,
            idempotency_key=execution.idempotency_key,
        )
        try:
            outcome = await asyncio.wait_for(executor.restart(target, timeout), timeout=timeout + 180)
            status = RemediationStatus.RESTARTED if outcome.succeeded else RemediationStatus.FAILED
            reason = None if outcome.succeeded else "executor reported failure"
            tail = outcome.output_tail
        except TimeoutError:
            status, reason, tail = (
                RemediationStatus.FAILED,
                "executor timed out; the restart may still complete",
                None,
            )
        except ExecutorUnavailable as exc:
            status, reason, tail = RemediationStatus.FAILED, f"executor unavailable: {exc}", None
        except Exception as exc:  # recorded, never raised into the void of a background task
            log.exception("restart_crashed", target_id=target.id)
            status, reason, tail = RemediationStatus.FAILED, f"executor error: {type(exc).__name__}", None
        await self._store.finish(
            execution.idempotency_key,
            status=status,
            finished_at=self._clock.now(),
            reason=reason,
            output_tail=(tail or "")[-OUTPUT_TAIL:] or None,
        )
        log.info("restart_finished", target_id=target.id, status=status.value, reason=reason)

    # ------------------------------------------------------------------------------------------ housekeeping
    async def reconcile(self) -> list[str]:
        """Mark IN_PROGRESS rows older than the target's timeout plus 5 minutes as FAILED (spec 10.3).
        Background tasks die on restarts, so their rows would otherwise stay in progress forever."""
        failed: list[str] = []
        now = self._clock.now()
        for target in self._registry.targets:
            cutoff = now - timedelta(seconds=target.policy.restart_timeout_seconds) - STALE_GRACE
            keys = await self._store.fail_stale(target.id, cutoff, "unknown outcome, may have completed")
            failed += keys  # in-process tasks are cut off at timeout + 180 s, before this cutoff
        if failed:
            log.warning("reconciler_marked_failed", keys=failed)
        return failed

    async def reset_breaker(self, operator: str, reason: str | None = None) -> None:
        await self._store.reset_breaker(operator=operator, reason=reason)
        log.warning("circuit_breaker_reset", operator=operator, reason=reason)  # audit line

    async def breaker_open(self) -> bool:
        return await self._breaker_open()

    async def drain(self, grace_seconds: float = 5) -> None:
        """Give running restarts a moment to finish on shutdown; the reconciler handles the rest."""
        if self._tasks:
            await asyncio.wait(list(self._tasks.values()), timeout=grace_seconds)

    # ------------------------------------------------------------------------------------------ helpers
    def _target(self, target_id: str) -> RestartTarget:
        target = self._registry.get(target_id)
        if target is None:
            raise PolicyViolation(f"unknown target: {target_id}")
        return target

    async def _breaker_open(self) -> bool:
        since = self._clock.now() - self._guard.breaker_window
        last_reset = await self._store.last_breaker_reset()
        if last_reset is not None and last_reset > since:
            since = last_reset
        return await self._store.failures_since(since) >= self._guard.breaker_failures

    def _approval_problem(self, req: RestartRequest) -> str | None:
        proof = req.approval
        if proof is None:
            return "approval required"
        if proof.mode not in self._guard.allowed_approval_modes:
            return f"approval mode {proof.mode} not allowed"
        if self._clock.now() - proof.approved_at > self._guard.approval_ttl:
            return "approval expired"
        return None

    async def _stored_duplicate(self, req: RestartRequest) -> RemediationResult:
        stored = await self._store.get(req.idempotency_key)
        if stored is None:  # pragma: no cover - the conflicting row exists by definition
            raise PolicyViolation("idempotency conflict without a stored row")
        return stored.to_result(duplicate=True)

    async def _reject(self, req: RestartRequest, reason: str) -> RemediationResult:
        await self._store.record_rejection(
            idempotency_key=req.idempotency_key,
            target_id=req.target_id,
            problem_id=req.problem_id,
            reason=reason,
        )
        log.info("restart_rejected", target_id=req.target_id, reason=reason)
        return RemediationResult(
            target_id=req.target_id,
            idempotency_key=req.idempotency_key,
            status=RemediationStatus.REJECTED,
            reason=reason,
        )
