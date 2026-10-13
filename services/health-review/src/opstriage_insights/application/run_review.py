"""Run a review end to end (OTA-APP-002 3.3 steps 1-9): prepare -> analyse -> validate -> report -> notify."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from opstriage_contracts.tools import NotificationKind, NotificationRequest, Severity
from opstriage_insights.application.collect import PrepareReview
from opstriage_insights.domain import findings as fnd
from opstriage_insights.domain import report as rep
from opstriage_insights.domain.model import ClusterScope
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.ports.analyst import Analyst, AnalystUnavailable
from opstriage_insights.ports.notifier import Notifier, NotifyFailed
from opstriage_insights.ports.store import ReviewKind, ReviewStore

SEVERITY = {
    "critical": Severity.CRITICAL,
    "high": Severity.WARNING,
    "medium": Severity.WARNING,
    "low": Severity.INFO,
}


@dataclass(frozen=True)
class ReviewOutcome:
    review_id: str
    report: dict[str, Any]
    notified: bool


@dataclass(frozen=True)
class NotifyPolicy:
    enabled: bool = True
    min_risk: str = "low"
    top_findings: int = 5
    web_base_url: str = "http://localhost:8120"


def card(report: dict[str, Any], policy: NotifyPolicy) -> NotificationRequest:
    """Summary card: top findings by risk and a link to the web view. Never raw log text."""
    findings = report["findings"][: policy.top_findings]
    risk = report["worst_risk"] or "low"
    lines = [report["summary"][:600]] + [f"[{f['risk'].upper()}] {f['title']}" for f in findings]
    by_risk = report["counts"]["by_risk"]
    return NotificationRequest(
        idempotency_key=f"health-review:{report['review_id']}",
        kind=NotificationKind.HEALTH_REVIEW,
        severity=SEVERITY.get(risk, Severity.INFO),
        title=f"Kafka health {'RCA' if report['kind'] == 'rca' else 'review'}: {report['cluster']}"[:200],
        summary="\n".join(lines)[:4000],
        facts={
            "Cluster": report["cluster"],
            "Findings": ", ".join(f"{k} {v}" for k, v in by_risk.items() if v) or "none",
            "Expires": report["expires_at"],
            "Model": report["model"] or "none (rules only)",
        },
        links={"Report": f"{policy.web_base_url.rstrip('/')}/ui/reports/{report['review_id']}"},
        channels=("teams",),
    )


class RunReview:
    def __init__(
        self,
        prepare: PrepareReview,
        analyst: Analyst | None,
        store: ReviewStore,
        notifier: Notifier | None,
        retention: RetentionPolicy,
        clock: Callable[[], datetime],
        *,
        concurrency: int = 1,
        notify: NotifyPolicy | None = None,
    ) -> None:
        self._prepare, self._analyst, self._store, self._notifier = prepare, analyst, store, notifier
        self._retention, self._clock = retention, clock
        self._gate = asyncio.Semaphore(concurrency)  # the model is shared (D5)
        self._notify = notify or NotifyPolicy()

    async def __call__(
        self, scope: ClusterScope, subject: dict[str, Any] | None = None, review_id: UUID | None = None
    ) -> ReviewOutcome:
        prepared = await self._prepare(scope, subject, review_id)
        digest = prepared.digest
        kind: ReviewKind = "rca" if subject else "review"
        model: str | None = None
        latency: int | None = None
        try:
            if self._analyst is None:
                validated = fnd.rules_only(digest, "analyst disabled (INSIGHTS__ANALYST__PROVIDER=none)")
            else:
                async with self._gate:
                    result = await self._analyst.analyse(digest.render(), kind)
                model, latency = result.model, result.latency_ms
                validated = fnd.validate(result.output, digest)
        except AnalystUnavailable as exc:
            validated = fnd.rules_only(digest, f"analyst unavailable: {exc}")
        except Exception as exc:
            await self._store.fail_review(prepared.review_id, type(exc).__name__)
            raise
        completed = self._clock()
        started = prepared.evidence.now
        meta = rep.ReportMeta(
            review_id=str(prepared.review_id),
            kind=kind,
            cluster=scope.name,
            generated_at=completed,
            expires_at=self._retention.report_expires_at(completed),
            model=model,
            latency_ms=latency,
            subject=subject,
        )
        document = rep.build(meta, digest, validated)
        await self._store.complete_review(
            prepared.review_id,
            completed_at=completed,
            review_expires_at=self._retention.review_expires_at(started, completed),
            evidence_expires_at=self._retention.evidence_expires_at(started, completed),
            report=document,
            markdown=rep.markdown(document),
            dropped_uncited=validated.dropped_uncited,
            guard_withheld=digest.guard_withheld,
        )
        return ReviewOutcome(str(prepared.review_id), document, await self._send(document))

    async def _send(self, document: dict[str, Any]) -> bool:
        if not (self._notify.enabled and self._notifier):
            return False
        worst = document["worst_risk"]
        if worst is None or fnd.RISK_ORDER[worst] < fnd.RISK_ORDER[self._notify.min_risk]:
            return False
        try:
            await self._notifier.send(card(document, self._notify))
        except NotifyFailed:
            return False  # the report is stored and viewable; the card is best effort
        return True
