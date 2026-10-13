"""Composition root for agent-runtime: the only module that knows concrete adapter classes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import httpx2
import yaml
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from opstriage_dynatrace.grail import DynatraceOAuth, GrailClient
from opstriage_llm import ChatJsonClient, auth_for
from opstriage_shared.clock import SystemClock
from opstriage_shared.credentials import TokenProvider, credentials_for
from opstriage_shared.db import Pool, connect, migrate, open_pool
from opstriage_triage.adapters.approvals_pg import PostgresApprovalStore
from opstriage_triage.adapters.auth_headers import (
    COGNITIVE_SCOPE,
    entra,
    no_auth,
    static_bearer,
    static_header,
)
from opstriage_triage.adapters.fake_context import FakeProblemContextSource
from opstriage_triage.adapters.grail_context import GrailProblemContextSource
from opstriage_triage.adapters.inbox_pg import PostgresInbox
from opstriage_triage.adapters.llm_decision import LlmDecisionModel
from opstriage_triage.adapters.mcp_tools import (
    McpEndpoint,
    McpNotificationGateway,
    McpRemediationGateway,
    McpTargetResolver,
    McpTicketingGateway,
    McpToolInvoker,
)
from opstriage_triage.adapters.rules_decision import RulesOnlyDecisionModel
from opstriage_triage.adapters.runs_pg import PostgresRunRepository
from opstriage_triage.adapters.summarizer_foundry import FoundrySummarizer
from opstriage_triage.adapters.systemone import SystemOneDecisionModel
from opstriage_triage.adapters.template_summarizer import TemplateSummarizer
from opstriage_triage.domain.policy import Policy
from opstriage_triage.entrypoints.worker import TriageWorker
from opstriage_triage.graph.build import TriageGraph, build_triage_graph
from opstriage_triage.graph.state import TriageDeps
from opstriage_triage.ports import DecisionModel, InboxPort, ProblemContextSource, Summarizer
from opstriage_triage.settings import Settings

MIGRATIONS = Path(__file__).resolve().parent / "migrations"  # shipped inside the package


def load_policy(path: Path) -> Policy:
    return Policy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def build_decision_model(
    settings: Settings, http: httpx2.AsyncClient, creds: TokenProvider | None = None
) -> DecisionModel:
    """`creds`: the process's shared credential (ADR 0013) for auth_mode=entra."""
    d = settings.triage.decision
    if d.provider == "rules":
        return RulesOnlyDecisionModel()
    if d.auth_mode == "bearer" and d.api_key is not None:
        auth = static_bearer(d.api_key.get_secret_value())
    elif d.auth_mode == "api_key" and d.api_key is not None:
        auth = static_header(d.api_key_header, d.api_key.get_secret_value())
    elif d.auth_mode == "entra":
        auth = entra(COGNITIVE_SCOPE, creds.token if creds is not None else None)
    else:
        auth = no_auth()
    if d.provider == "llm":
        return LlmDecisionModel(
            http,
            url=f"{d.base_url.rstrip('/')}{d.path}",
            model=d.model,
            runbook=d.runbook_file.read_text(encoding="utf-8"),
            auth_header=auth,
            timeout_seconds=d.timeout_seconds,
            max_tokens=d.max_tokens,
            response_format=d.response_format,
            extra_body=d.extra_body,
        )
    return SystemOneDecisionModel(
        http,
        url=f"{d.base_url.rstrip('/')}{d.path}",
        model=d.model,
        auth_header=auth,
        timeout_seconds=d.timeout_seconds,
        shuffle_options=d.shuffle_options,
    )


def build_context_source(
    settings: Settings, http: httpx2.AsyncClient, inbox: InboxPort
) -> ProblemContextSource:
    c = settings.triage.context
    if c.provider == "grail":
        return GrailProblemContextSource(
            GrailClient(http, DynatraceOAuth(http, c.grail), c.grail),
            max_log_lines=c.max_log_lines,
            log_window_minutes=c.log_window_minutes,
            problem_lookback_days=c.problem_lookback_days,
            timeout_seconds=c.timeout_seconds,
        )
    return FakeProblemContextSource(c.fixtures_dir, inbox, c.max_log_lines)


def build_summarizer(
    settings: Settings, http: httpx2.AsyncClient, creds: TokenProvider | None = None
) -> Summarizer:
    s = settings.triage.summarizer
    template = TemplateSummarizer()
    if s.provider != "foundry":
        return template
    client = ChatJsonClient(http, s, auth_for(s, creds.token if creds is not None else None))
    return FoundrySummarizer(client, template, max_chars=s.max_chars)


@dataclass(slots=True)
class Container:
    pool: Pool
    checkpoint_pool: Pool
    http: httpx2.AsyncClient
    inbox: PostgresInbox
    graph: TriageGraph
    deps: TriageDeps
    worker: TriageWorker
    creds: TokenProvider

    async def close(self) -> None:
        await self.inbox.close()
        await self.http.aclose()
        await self.checkpoint_pool.close()
        await self.pool.close()
        await self.creds.close()


async def build_container(settings: Settings) -> Container:
    t = settings.triage
    policy = await asyncio.to_thread(load_policy, t.policy_file)
    creds = credentials_for(settings)  # one per process (ADR 0013)
    credential = getattr(creds, "credential", None)  # azure-identity credential on PLATFORM=azure
    pool = await open_pool(settings.db, credential=credential)
    await migrate(pool, "triage", MIGRATIONS)
    async with pool.connection() as conn:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
    checkpoint_pool = await open_pool(settings.db, search_path="langgraph", credential=credential)
    checkpointer = AsyncPostgresSaver(conn=checkpoint_pool)
    await checkpointer.setup()

    inbox = PostgresInbox(
        pool,
        lambda: connect(settings.db, credential=credential),
        lease_seconds=t.inbox.lease_seconds,
        max_attempts=t.inbox.max_attempts,
        retry_backoff_seconds=t.inbox.retry_backoff_seconds,
    )
    invoker = McpToolInvoker(
        {
            "notification": McpEndpoint(t.mcp.notification_url, t.mcp.notification_token),
            "ticketing": McpEndpoint(t.mcp.ticketing_url, t.mcp.ticketing_token),
            "remediation": McpEndpoint(t.mcp.remediation_url, t.mcp.remediation_token),
        },
        t.mcp.timeout_seconds,
    )
    clock = SystemClock()
    http = httpx2.AsyncClient()
    deps = TriageDeps(
        decision_model=build_decision_model(settings, http, creds),
        context_source=build_context_source(settings, http, inbox),
        notifications=McpNotificationGateway(invoker),
        tickets=McpTicketingGateway(invoker),
        remediation=McpRemediationGateway(invoker),
        approvals=PostgresApprovalStore(pool, t.approval.mode),
        summarizer=build_summarizer(settings, http, creds),
        policy=policy,
        targets=McpTargetResolver(invoker),
        runs=PostgresRunRepository(pool),
        inbox=inbox,
        clock=clock,
        sleep=asyncio.sleep,
        run_url_base=t.public_base_url,
        max_state_chars=t.context.max_state_chars,
        rca_url_base=t.insights_base_url,
    )
    graph = build_triage_graph(checkpointer)
    worker = TriageWorker(
        inbox,
        graph,
        deps,
        run_timeout_s=t.run_timeout_seconds,
        idle_wait_s=t.inbox.idle_wait_seconds,
        approval_ttl=timedelta(minutes=t.approval.ttl_minutes),
        sweep_interval_s=t.approval.sweep_interval_seconds,
    )
    return Container(pool, checkpoint_pool, http, inbox, graph, deps, worker, creds)
