"""Inbox worker (spec 12.2.5): N claim loops plus one sweeper/resumer loop."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from opstriage_contracts.tools import NotificationKind, NotificationRequest, Severity
from opstriage_shared.logging import bound_contextvars
from opstriage_triage.graph.build import TriageGraph
from opstriage_triage.graph.state import TriageDeps
from opstriage_triage.ports import ApprovalRecord, InboxPort

log = structlog.get_logger()


def _config(run_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": run_id}}


class TriageWorker:
    def __init__(
        self,
        inbox: InboxPort,
        graph: TriageGraph,
        deps: TriageDeps,
        *,
        run_timeout_s: float,
        idle_wait_s: float,
        approval_ttl: timedelta,
        sweep_interval_s: float,
    ) -> None:
        self._inbox, self._graph, self._deps = inbox, graph, deps
        self._run_timeout, self._idle_wait = run_timeout_s, idle_wait_s
        self._approval_ttl, self._sweep_interval = approval_ttl, sweep_interval_s
        self._poke = asyncio.Event()

    def poke(self) -> None:
        """Wake the resumer now (a decision was just recorded)."""
        self._poke.set()

    # ------------------------------------------------------------------------------------------ claim loop
    async def run_claim_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                alert = await self._inbox.claim_next()
            except Exception:
                log.exception("inbox_claim_failed")
                await asyncio.sleep(self._idle_wait)
                continue
            if alert is None:
                await self._inbox.wait_for_notify(max_wait=self._idle_wait)
                continue
            run_id = str(alert.alert_id)
            with bound_contextvars(run_id=run_id, alert_id=run_id, problem_id=alert.event.problem_id):
                await self._run_one(
                    alert.alert_id,
                    {
                        "run_id": run_id,
                        "event": alert.event.model_dump(mode="json"),
                        "outcomes": [],
                    },
                )

    async def _run_one(self, alert_id: UUID, graph_input: dict[str, Any]) -> None:
        config = _config(str(alert_id))
        try:
            snapshot = await self._graph.aget_state(config)
            if snapshot.values and not snapshot.next:  # finished earlier; crashed before mark()
                await self._inbox.mark(alert_id, "done")
                return
            run_input = None if snapshot.next else graph_input  # unfinished checkpoint: resume it
            log.info("triage_run_started", resumed=run_input is None)
            async with asyncio.timeout(self._run_timeout):
                result = await self._graph.ainvoke(run_input, config, context=self._deps, durability="sync")
            interrupted = "__interrupt__" in result
            await self._inbox.mark(alert_id, "awaiting_approval" if interrupted else "done")
            log.info("triage_run_finished", awaiting_approval=interrupted)
        except Exception as exc:  # recorded; fail() decides between retry and failed
            log.exception("triage_run_failed")
            if await self._inbox.fail(alert_id, error=type(exc).__name__) == "failed":
                await self._escalate_failure(alert_id, type(exc).__name__)

    async def _escalate_failure(self, alert_id: UUID, error: str) -> None:
        event = await self._inbox.get_event(alert_id)
        label = (event.display_id or event.problem_id) if event else str(alert_id)
        try:
            await self._deps.notifications.send(
                NotificationRequest(
                    idempotency_key=f"{alert_id}:notify:run-failed",
                    kind=NotificationKind.ESCALATION,
                    severity=Severity.CRITICAL,
                    title=f"ESCALATION [{label}] triage failed after retries"[:200],
                    summary=f"The triage run failed on its last attempt ({error}). Triage it by hand.",
                    links={"Triage run": self._deps.run_url(str(alert_id))},
                )
            )
        except Exception:
            log.exception("failure_escalation_failed")

    # ------------------------------------------------------------------------------------------ sweeper
    async def run_sweeper(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.sweep_once()
            except Exception:
                log.exception("sweep_failed")
            self._poke.clear()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._sweep_interval):
                    await self._poke.wait()

    async def sweep_once(self) -> None:
        for alert_id in await self._inbox.sweep_expired():
            await self._escalate_failure(alert_id, "lease expired on the last attempt")
        await self._deps.approvals.expire_older_than(self._deps.clock.now() - self._approval_ttl)
        for record in await self._deps.approvals.decided_not_resumed():
            with bound_contextvars(run_id=record.run_id, problem_id=record.problem_id):
                await self.resume(record)

    async def resume(self, record: ApprovalRecord) -> None:
        config = _config(record.run_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.next:
            await self._deps.approvals.mark_resumed(record.run_id)
            return
        value = {
            "approved": record.state == "approved",
            "approval_id": str(record.approval_id),
            "approved_by": record.decided_by or "unknown",
            "approved_at": (record.decided_at or self._deps.clock.now()).isoformat(),
            "mode": "manual_api",
            "reason": record.reason or record.state,
            "state": record.state,
        }
        log.info("approval_resuming", state=record.state)
        async with asyncio.timeout(self._run_timeout):
            result = await self._graph.ainvoke(
                Command(resume=value), config, context=self._deps, durability="sync"
            )
        await self._deps.approvals.mark_resumed(record.run_id)
        await self._inbox.mark(
            UUID(record.run_id), "awaiting_approval" if "__interrupt__" in result else "done"
        )
