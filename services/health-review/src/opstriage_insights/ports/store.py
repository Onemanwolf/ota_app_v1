"""Persistence port for reviews, collected evidence and (5.5) reports. Schema: insights (migration 001)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

ReviewKind = Literal["review", "rca"]
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
