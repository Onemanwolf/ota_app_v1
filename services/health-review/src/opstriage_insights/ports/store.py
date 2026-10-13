"""Persistence port for reviews, collected evidence and (5.5) reports. Schema: insights (migration 001)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

ReviewKind = Literal["review", "rca"]
ReviewStatus = Literal["running", "completed", "failed"]


@dataclass(frozen=True, slots=True)
class ReviewRow:
    review_id: UUID
    kind: ReviewKind
    cluster: str
    status: ReviewStatus
    started_at: datetime
    completed_at: datetime | None
    expires_at: datetime
    worst_risk: str | None = None
    by_risk: dict[str, int] | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PurgeCounts:
    evidence_deleted: int
    reports_deleted: int
    reviews_deleted: int  # tombstones older than the tombstone period
    reviews_expired: int  # content removed, tombstone kept so links answer 410


@dataclass(frozen=True, slots=True)
class StoredReport:
    review_id: UUID
    expired: bool
    expires_at: datetime
    report: dict[str, Any] | None
    markdown: str | None


EvidenceSource = Literal["logs", "metrics", "problems"]


class ReviewStore(Protocol):
    async def start_review(
        self,
        review_id: UUID,
        kind: ReviewKind,
        cluster: str,
        subject: dict[str, Any] | None,
        started_at: datetime,
        expires_at: datetime,
    ) -> None: ...

    async def save_evidence(
        self,
        review_id: UUID,
        source: EvidenceSource,
        collected_at: datetime,
        expires_at: datetime,
        bundle: dict[str, Any],
    ) -> None: ...

    async def fail_review(self, review_id: UUID, error: str) -> None: ...

    async def complete_review(
        self,
        review_id: UUID,
        *,
        completed_at: datetime,
        review_expires_at: datetime,
        evidence_expires_at: datetime,
        report: dict[str, Any],
        markdown: str,
        dropped_uncited: int,
        guard_withheld: int,
    ) -> None:
        """Mark completed, store the report and shorten evidence expiry (retention: completion + 4h)."""
        ...

    async def get_report(self, review_id: UUID, now: datetime) -> StoredReport | None:
        """None when unknown. An expired report is returned with `expired=True` and no content."""
        ...

    async def get_review(self, review_id: UUID) -> ReviewRow | None: ...

    async def list_reports(
        self, now: datetime, cluster: str | None = None, limit: int = 50
    ) -> list[ReviewRow]:
        """Unexpired reviews, newest first (running and failed ones included, with their status)."""
        ...

    async def purge(self, now: datetime, tombstone: timedelta) -> PurgeCounts:
        """Delete expired evidence and reports; blank expired reviews to tombstones (no content) and delete
        tombstones older than `tombstone`; record the counts (never content) in the purge log."""
        ...

    async def expire_review(self, review_id: UUID, now: datetime) -> bool:
        """Local/test only: make one review, its evidence and report expire at `now`."""
        ...
