"""Collect -> redact -> digest (OTA-APP-002 3.3 steps 2-5). The analyst step follows in 5.5."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from opstriage_insights.domain import digest as digest_mod
from opstriage_insights.domain import kafka_metrics as km
from opstriage_insights.domain.baselines import KAFKA_BASELINES, BaselineRule, evaluate
from opstriage_insights.domain.log_templates import LogTemplate, mine
from opstriage_insights.domain.model import ClusterScope, LogRecord, MetricSeries, ProblemRecord
from opstriage_insights.domain.redaction import Redactor
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.ports.guard import Guard, GuardUnavailable
from opstriage_insights.ports.sources import LogSource, MetricSource, ProblemSource
from opstriage_insights.ports.store import ReviewKind, ReviewStore

DAY_STEP, MONTH_STEP = timedelta(minutes=5), timedelta(hours=1)
PROBLEM_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class CollectedEvidence:
    scope: ClusterScope
    now: datetime
    logs: tuple[LogRecord, ...]  # content already redacted
    day_series: Mapping[str, Sequence[MetricSeries]]
    month_series: Mapping[str, Sequence[MetricSeries]]
    problems: tuple[ProblemRecord, ...]
    redactions: Mapping[str, int]
    window: tuple[datetime, datetime] | None = None  # RCA: analysis window (now = its end)


class CollectEvidence:
    def __init__(
        self,
        logs: LogSource,
        metrics: MetricSource,
        problems: ProblemSource,
        redactor: Redactor,
        *,
        log_window: timedelta = timedelta(hours=24),
        trend_window: timedelta = timedelta(days=30),
        max_log_records: int = 50_000,
        metric_keys: Sequence[str] = km.REVIEW_METRICS,
    ) -> None:
        self._logs, self._metrics, self._problems, self._redactor = logs, metrics, problems, redactor
        self._log_window, self._trend_window, self._max_logs = log_window, trend_window, max_log_records
        self._keys = tuple(metric_keys)

    async def __call__(
        self, scope: ClusterScope, now: datetime, window: tuple[datetime, datetime] | None = None
    ) -> CollectedEvidence:
        """A daily review looks back from `now`. An RCA looks at `window`: its end becomes `now`, and logs
        start 24 h before the window so templates that are new inside it can be told apart."""
        if window is not None:
            now = window[1]
        day_start, month_start = now - timedelta(hours=24), now - self._trend_window
        log_start = (window[0] - self._log_window) if window else now - self._log_window

        async def series(start: datetime, step: timedelta) -> dict[str, list[MetricSeries]]:
            results = await asyncio.gather(
                *(self._metrics.fetch(scope, k, start, now, step) for k in self._keys)
            )
            return dict(zip(self._keys, results, strict=True))

        raw_logs, day, month, problems = await asyncio.gather(
            self._logs.fetch(scope, log_start, now, self._max_logs),
            series(day_start, DAY_STEP),
            series(month_start, MONTH_STEP),
            self._problems.fetch(scope, now - PROBLEM_WINDOW, now),
        )
        counts: Counter[str] = Counter()
        logs = []
        for r in raw_logs:
            red = self._redactor.redact(r.content)
            counts.update(red.counts)
            logs.append(replace(r, content=red.text))
        return CollectedEvidence(scope, now, tuple(logs), day, month, tuple(problems), dict(counts), window)


def _identity(text: str) -> str:
    return text


class BuildDigest:
    def __init__(
        self,
        guard: Guard,
        *,
        max_tokens: int = 12_000,
        sparkline_points: int = 96,
        rules: Sequence[BaselineRule] = KAFKA_BASELINES,
    ) -> None:
        self._guard, self._max_tokens, self._points, self._rules = guard, max_tokens, sparkline_points, rules

    async def __call__(self, ev: CollectedEvidence) -> digest_mod.Digest:
        templates = mine(ev.logs, _identity)  # already redacted at collection
        withheld, available = await self._screen(templates)
        return digest_mod.build(
            ev.scope.name,
            ev.now,
            day_series=ev.day_series,
            month_series=ev.month_series,
            breaches=evaluate(self._rules, ev.day_series),
            templates=templates,
            withheld=withheld,
            problems=ev.problems,
            guard_available=available,
            redactions=ev.redactions,
            max_tokens=self._max_tokens,
            sparkline_points=self._points,
            rca_window=ev.window,
        )

    async def _screen(self, templates: Sequence[LogTemplate]) -> tuple[set[str], bool]:
        """Screen each template with its examples. Guard down: withhold everything (fail closed)."""
        if not templates:
            return set(), True
        # Screen the template and each example separately: concatenated text skews the classifier.
        pieces = [(t.key, text) for t in templates for text in (t.text, *t.examples)]
        try:
            flags = await self._guard.flags([text for _, text in pieces])
        except GuardUnavailable:
            return {t.key for t in templates}, False
        return {key for (key, _), flagged in zip(pieces, flags, strict=True) if flagged}, True


@dataclass(frozen=True)
class PreparedReview:
    review_id: UUID
    evidence: CollectedEvidence
    digest: digest_mod.Digest
    started_at: (
        datetime  # when collection ran; retention counts from here (an RCA window may lie in the past)
    )


class PrepareReview:
    """Create the review row, collect and store evidence (28h expiry until completion), build the digest."""

    def __init__(
        self,
        store: ReviewStore,
        collect: CollectEvidence,
        build_digest: BuildDigest,
        retention: RetentionPolicy,
        clock: Callable[[], datetime],
        new_id: Callable[[], UUID],
        serialize: Callable[[CollectedEvidence], Mapping[str, dict[str, Any]]],
    ) -> None:
        self._store, self._collect, self._digest = store, collect, build_digest
        self._retention, self._clock, self._new_id, self._serialize = retention, clock, new_id, serialize

    async def __call__(
        self,
        scope: ClusterScope,
        subject: dict[str, Any] | None = None,
        review_id: UUID | None = None,
        window: tuple[datetime, datetime] | None = None,
    ) -> PreparedReview:
        review_id, started = review_id or self._new_id(), self._clock()
        kind: ReviewKind = "rca" if subject else "review"
        await self._store.start_review(
            review_id, kind, scope.name, subject, started, self._retention.review_expires_at(started, None)
        )
        try:
            evidence = await self._collect(scope, started, window)
            expires = self._retention.evidence_expires_at(started, None)
            for source, bundle in self._serialize(evidence).items():
                await self._store.save_evidence(review_id, source, started, expires, bundle)  # type: ignore[arg-type]
            digest = await self._digest(evidence)
        except Exception as exc:
            await self._store.fail_review(review_id, type(exc).__name__)
            raise
        return PreparedReview(review_id, evidence, digest, started)
