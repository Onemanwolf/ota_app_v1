"""ReviewStore adapters: PostgreSQL (schema insights) and an in-memory fake for tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import sql
from psycopg.types.json import Jsonb

from opstriage_insights.ports.store import EvidenceSource, PurgeCounts, ReviewKind, ReviewRow, StoredReport
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
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE insights.reviews SET status = 'completed', completed_at = %s, expires_at = %s, "
                "dropped_uncited = %s, guard_withheld = %s WHERE id = %s",
                (completed_at, review_expires_at, dropped_uncited, guard_withheld, review_id),
            )
            await conn.execute(
                "INSERT INTO insights.reports (review_id, completed_at, expires_at, report, markdown) "
                "VALUES (%s, %s, %s, %s, %s)",
                (review_id, completed_at, review_expires_at, Jsonb(report), markdown),
            )
            await conn.execute(
                "UPDATE insights.evidence SET expires_at = LEAST(expires_at, %s) WHERE review_id = %s",
                (evidence_expires_at, review_id),
            )

    async def get_report(self, review_id: UUID, now: datetime) -> StoredReport | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT r.expires_at, p.report, p.markdown FROM insights.reviews r "
                "LEFT JOIN insights.reports p ON p.review_id = r.id WHERE r.id = %s",
                (review_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:  # the purge job may not have run yet: never serve expired content
            return StoredReport(review_id, True, row["expires_at"], None, None)
        return StoredReport(review_id, False, row["expires_at"], row["report"], row["markdown"])

    async def get_review(self, review_id: UUID) -> ReviewRow | None:
        rows = await self._rows("WHERE r.id = %s", (review_id,), 1)
        return rows[0] if rows else None

    async def list_reports(
        self, now: datetime, cluster: str | None = None, limit: int = 50
    ) -> list[ReviewRow]:
        if cluster:
            return await self._rows("WHERE r.expires_at > %s AND r.cluster = %s", (now, cluster), limit)
        return await self._rows("WHERE r.expires_at > %s", (now,), limit)

    async def _rows(self, where: str, params: tuple[Any, ...], limit: int) -> list[ReviewRow]:
        """`where` is one of the code constants above; every value is a bound parameter."""
        query = sql.SQL(
            "SELECT r.id, r.kind, r.cluster, r.status, r.started_at, r.completed_at, r.expires_at, r.error, "
            "p.report->>'worst_risk' AS worst_risk, p.report->'counts'->'by_risk' AS by_risk "
            "FROM insights.reviews r LEFT JOIN insights.reports p ON p.review_id = r.id "
            "{where} ORDER BY r.started_at DESC LIMIT %s"
        ).format(where=sql.SQL(where))
        async with self._pool.connection() as conn:
            cur = await conn.execute(query, (*params, limit))
            rows = await cur.fetchall()
        return [
            ReviewRow(
                r["id"],
                r["kind"],
                r["cluster"],
                r["status"],
                r["started_at"],
                r["completed_at"],
                r["expires_at"],
                r["worst_risk"],
                r["by_risk"],
                r["error"],
            )
            for r in rows
        ]

    async def purge(self, now: datetime, tombstone: timedelta) -> PurgeCounts:
        async with self._pool.connection() as conn, conn.transaction():
            ev = await conn.execute("DELETE FROM insights.evidence WHERE expires_at <= %s", (now,))
            rp = await conn.execute("DELETE FROM insights.reports WHERE expires_at <= %s", (now,))
            ex = await conn.execute(
                "UPDATE insights.reviews SET subject = NULL, error = NULL "
                "WHERE expires_at <= %s AND (subject IS NOT NULL OR error IS NOT NULL)",
                (now,),
            )
            rv = await conn.execute("DELETE FROM insights.reviews WHERE expires_at <= %s", (now - tombstone,))
            counts = PurgeCounts(ev.rowcount, rp.rowcount, rv.rowcount, ex.rowcount)
            await conn.execute(
                "INSERT INTO insights.purge_log (ran_at, reviews_deleted, evidence_deleted, reports_deleted) "
                "VALUES (%s, %s, %s, %s)",
                (now, counts.reviews_deleted, counts.evidence_deleted, counts.reports_deleted),
            )
            await conn.execute("DELETE FROM insights.purge_log WHERE ran_at <= %s", (now - tombstone,))
        return counts

    async def expire_review(self, review_id: UUID, now: datetime) -> bool:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE insights.reviews SET expires_at = %s WHERE id = %s", (now, review_id)
            )
            await conn.execute(
                "UPDATE insights.reports SET expires_at = %s WHERE review_id = %s", (now, review_id)
            )
            await conn.execute(
                "UPDATE insights.evidence SET expires_at = %s WHERE review_id = %s", (now, review_id)
            )
        return cur.rowcount == 1


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

    reports: dict[UUID, dict[str, Any]] = field(default_factory=dict)

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
        self.reviews[review_id].update(
            status="completed", completed_at=completed_at, expires_at=review_expires_at
        )
        self.reports[review_id] = {"report": report, "markdown": markdown, "expires_at": review_expires_at}
        for e in self.evidence:
            if e["review_id"] == review_id:
                e["expires_at"] = min(e["expires_at"], evidence_expires_at)

    async def get_report(self, review_id: UUID, now: datetime) -> StoredReport | None:
        review = self.reviews.get(review_id)
        if review is None:
            return None
        if review["expires_at"] <= now:
            return StoredReport(review_id, True, review["expires_at"], None, None)
        rep = self.reports.get(review_id)
        if rep is None:
            return StoredReport(review_id, False, review["expires_at"], None, None)
        return StoredReport(review_id, False, rep["expires_at"], rep["report"], rep["markdown"])

    async def get_review(self, review_id: UUID) -> ReviewRow | None:
        return self._row(review_id) if review_id in self.reviews else None

    async def list_reports(
        self, now: datetime, cluster: str | None = None, limit: int = 50
    ) -> list[ReviewRow]:
        rows = [
            self._row(i)
            for i, r in self.reviews.items()
            if r["expires_at"] > now and (cluster is None or r["cluster"] == cluster)
        ]
        return sorted(rows, key=lambda r: r.started_at, reverse=True)[:limit]

    def _row(self, review_id: UUID) -> ReviewRow:
        r = self.reviews[review_id]
        rep = self.reports.get(review_id, {}).get("report") or {}
        return ReviewRow(
            review_id,
            r["kind"],
            r["cluster"],
            r["status"],
            r["started_at"],
            r.get("completed_at"),
            r["expires_at"],
            rep.get("worst_risk"),
            (rep.get("counts") or {}).get("by_risk"),
            r.get("error"),
        )

    purge_log: list[dict[str, Any]] = field(default_factory=list)

    async def purge(self, now: datetime, tombstone: timedelta) -> PurgeCounts:
        before = len(self.evidence)
        self.evidence = [e for e in self.evidence if e["expires_at"] > now]
        expired_reports = [k for k, r in self.reports.items() if r["expires_at"] <= now]
        for k in expired_reports:
            del self.reports[k]
        blanked = 0
        for r in self.reviews.values():
            if r["expires_at"] <= now and (r.get("subject") is not None or r.get("error") is not None):
                r["subject"], r["error"] = None, None
                blanked += 1
        old = [k for k, r in self.reviews.items() if r["expires_at"] <= now - tombstone]
        for k in old:
            del self.reviews[k]
        counts = PurgeCounts(before - len(self.evidence), len(expired_reports), len(old), blanked)
        self.purge_log.append({"ran_at": now, **asdict(counts)})
        return counts

    async def expire_review(self, review_id: UUID, now: datetime) -> bool:
        if review_id not in self.reviews:
            return False
        self.reviews[review_id]["expires_at"] = now
        if review_id in self.reports:
            self.reports[review_id]["expires_at"] = now
        for e in self.evidence:
            if e["review_id"] == review_id:
                e["expires_at"] = now
        return True
