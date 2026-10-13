"""ReviewStore adapters: PostgreSQL (schema insights) and an in-memory fake for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from opstriage_insights.ports.store import EvidenceSource, ReviewKind
from opstriage_shared.db import Pool


class PostgresReviewStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def start_review(
        self,
        review_id: UUID,
        kind: ReviewKind,
        cluster: str,
        subject: dict[str, Any] | None,
        started_at: datetime,
        expires_at: datetime,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO insights.reviews (id, kind, cluster, subject, started_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    review_id,
                    kind,
                    cluster,
                    Jsonb(subject) if subject is not None else None,
                    started_at,
                    expires_at,
                ),
            )

    async def save_evidence(
        self,
        review_id: UUID,
        source: EvidenceSource,
        collected_at: datetime,
        expires_at: datetime,
        bundle: dict[str, Any],
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO insights.evidence (review_id, source, collected_at, expires_at, bundle) "
                "VALUES (%s, %s, %s, %s, %s)",
                (review_id, source, collected_at, expires_at, Jsonb(bundle)),
            )

    async def fail_review(self, review_id: UUID, error: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE insights.reviews SET status = 'failed', error = %s "
                "WHERE id = %s AND status = 'running'",
                (error[:200], review_id),
            )


@dataclass
class MemoryReviewStore:
    reviews: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    async def start_review(
        self,
        review_id: UUID,
        kind: ReviewKind,
        cluster: str,
        subject: dict[str, Any] | None,
        started_at: datetime,
        expires_at: datetime,
    ) -> None:
        self.reviews[review_id] = {
            "kind": kind,
            "cluster": cluster,
            "subject": subject,
            "status": "running",
            "started_at": started_at,
            "expires_at": expires_at,
        }

    async def save_evidence(
        self,
        review_id: UUID,
        source: EvidenceSource,
        collected_at: datetime,
        expires_at: datetime,
        bundle: dict[str, Any],
    ) -> None:
        self.evidence.append(
            {
                "review_id": review_id,
                "source": source,
                "collected_at": collected_at,
                "expires_at": expires_at,
                "bundle": bundle,
            }
        )

    async def fail_review(self, review_id: UUID, error: str) -> None:
        self.reviews[review_id].update(status="failed", error=error)
