"""Composition root: the only module that knows concrete adapter classes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2

from opstriage_dynatrace.grail import DynatraceOAuth, GrailClient
from opstriage_insights.adapters.cluster_config import fixture_clusters, load_clusters
from opstriage_insights.adapters.evidence_json import serialize
from opstriage_insights.adapters.fixtures import (
    FixtureLogSource,
    FixtureMetricSource,
    FixtureProblemSource,
    FixtureStore,
)
from opstriage_insights.adapters.grail_sources import GrailLogSource, GrailMetricSource, GrailProblemSource
from opstriage_insights.adapters.guard import HttpGuard, KeywordGuard
from opstriage_insights.adapters.llm_analyst import LlmAnalyst
from opstriage_insights.adapters.notifier import McpNotifier
from opstriage_insights.adapters.stores import PostgresReviewStore
from opstriage_insights.application.collect import BuildDigest, CollectEvidence, PrepareReview
from opstriage_insights.application.purge import Purge
from opstriage_insights.application.run_rca import PlanRca
from opstriage_insights.application.run_review import NotifyPolicy, RunReview
from opstriage_insights.domain.model import ClusterScope
from opstriage_insights.domain.redaction import Redactor
from opstriage_insights.domain.retention import RetentionPolicy
from opstriage_insights.entrypoints.api import Runner
from opstriage_insights.entrypoints.scheduler import Scheduler
from opstriage_insights.ports.analyst import Analyst
from opstriage_insights.ports.guard import Guard
from opstriage_insights.ports.notifier import Notifier
from opstriage_insights.ports.sources import LogSource, MetricSource, ProblemSource
from opstriage_insights.ports.store import ReviewStore
from opstriage_insights.settings import Settings
from opstriage_llm import ChatJsonClient
from opstriage_shared.db import Pool, migrate, open_pool

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


@dataclass(slots=True)
class Container:
    pool: Pool
    source: str  # "fake" | "grail"
    clusters: dict[str, ClusterScope]
    prepare: PrepareReview
    store: ReviewStore
    runner: Runner
    plan_rca: PlanRca
    purge: Purge
    scheduler: Scheduler | None = None
    http: httpx2.AsyncClient | None = None

    async def close(self) -> None:
        if self.scheduler is not None:
            await self.scheduler.stop()
        await self.runner.drain()
        if self.http is not None:
            await self.http.aclose()
        await self.pool.close()


def _now() -> datetime:
    return datetime.now(UTC)


def build_sources(
    settings: Settings, http: httpx2.AsyncClient
) -> tuple[LogSource, MetricSource, ProblemSource, dict[str, ClusterScope]]:
    i = settings.insights
    clusters = load_clusters(i.config_file)
    if i.source == "fake":
        store = FixtureStore(i.fixtures_dir)
        return (
            FixtureLogSource(store),
            FixtureMetricSource(store),
            FixtureProblemSource(store),
            {**clusters, **fixture_clusters(store)},
        )
    client = GrailClient(http, DynatraceOAuth(http, i.grail), i.grail)
    return GrailLogSource(client), GrailMetricSource(client), GrailProblemSource(client), clusters


def build_guard(settings: Settings, http: httpx2.AsyncClient) -> Guard:
    g = settings.insights.guard
    return (
        KeywordGuard()
        if g.provider == "keyword"
        else HttpGuard(http, g.base_url, g.timeout_seconds, g.min_score)
    )


def build_analyst(settings: Settings, http: httpx2.AsyncClient) -> Analyst | None:
    a = settings.insights.analyst
    if a.provider == "none":
        return None
    runbook = a.runbook_file.read_text(encoding="utf-8") if a.runbook_file.is_file() else ""
    return LlmAnalyst(ChatJsonClient(http, a), runbook)


def build_notifier(settings: Settings) -> Notifier | None:
    n = settings.insights.notify
    if not (n.enabled and n.mcp_token.get_secret_value()):
        return None
    return McpNotifier(n.mcp_url, n.mcp_token, n.timeout_seconds)


async def build_container(settings: Settings, migrations_dir: Path = MIGRATIONS) -> Container:
    i = settings.insights
    pool = await open_pool(settings.db)
    await migrate(pool, "insights", migrations_dir)
    http = httpx2.AsyncClient()
    logs, metrics, problems, clusters = build_sources(settings, http)
    collect = CollectEvidence(
        logs,
        metrics,
        problems,
        Redactor(),
        log_window=i.collect.log_window,
        trend_window=i.collect.trend_window,
        max_log_records=i.collect.max_log_records,
    )
    digest = BuildDigest(
        build_guard(settings, http), max_tokens=i.digest.max_tokens, sparkline_points=i.web.sparkline_points
    )
    r = i.retention
    retention = RetentionPolicy(r.report_ttl, r.evidence_max_age, r.evidence_after_report)
    store = PostgresReviewStore(pool)
    prepare = PrepareReview(store, collect, digest, retention, _now, uuid.uuid4, serialize)
    notify = NotifyPolicy(i.notify.enabled, i.notify.min_risk, i.notify.top_findings, i.web.base_url)
    run = RunReview(
        prepare,
        build_analyst(settings, http),
        store,
        build_notifier(settings),
        retention,
        _now,
        concurrency=i.analyst.concurrency,
        notify=notify,
    )
    runner = Runner(run)
    purge = Purge(store, _now, r.tombstone)
    return Container(
        pool=pool,
        source=i.source,
        clusters=clusters,
        prepare=prepare,
        store=store,
        runner=runner,
        plan_rca=PlanRca(problems, clusters, _now),
        purge=purge,
        scheduler=Scheduler(
            purge,
            runner,
            [clusters[c] for c in i.schedule.clusters if c in clusters],
            purge_interval=r.purge_interval,
            daily_at=i.schedule.daily_at,
            tz=ZoneInfo(i.schedule.timezone),
            clock=_now,
        ),
        http=http,
    )
