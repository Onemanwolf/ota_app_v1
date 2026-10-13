"""ProblemContextSource contract: the fake and the Grail adapter behave the same for the graph."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_contracts.tools import ResolveTargetResult, TargetSummary
from opstriage_dynatrace.grail import DynatraceOAuth, GrailClient, GrailSettings
from opstriage_triage.adapters.fake_context import FakeProblemContextSource
from opstriage_triage.adapters.grail_context import (
    GrailProblemContextSource,
    dedupe_by_template,
    log_line,
    logs_dql,
    problem_dql,
)
from opstriage_triage.ports import ProblemContextSource

REPO = Path(__file__).resolve().parents[3]
CONTEXT = REPO / "fixtures" / "context"
GRAIL_PROBLEM = json.loads((REPO / "fixtures/dynatrace/grail_records/kafka_broker_oom.json").read_text())
PROBLEM_ID = GRAIL_PROBLEM["event.id"]
NOW = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)
GRAIL = GrailSettings(
    environment_url="https://abc123.apps.dynatrace.com",
    oauth_client_id="id",
    oauth_client_secret=SecretStr("secret"),
)
TARGET = TargetSummary(
    target_id="kafka-1",
    description="broker",
    executor="docker_local",
    autonomy="approval_required",
    container="kafka",
    host_entity_ids=("HOST-91C2D4E5F6A70811",),
)
LOG_RECORDS = [
    {
        "timestamp": "2026-10-12T14:11:09.000000000Z",
        "status": "ERROR",
        "content": "OutOfMemoryError: heap 4096",
    },
    {
        "timestamp": "2026-10-12T14:11:08.000000000Z",
        "status": "ERROR",
        "content": "OutOfMemoryError: heap 2048",
    },
    {
        "timestamp": "2026-10-12T14:11:07.000000000Z",
        "status": "WARN",
        "content": "ISR shrink for topic orders",
    },
    {"timestamp": "2026-10-12T14:11:06.000000000Z", "status": "WARN", "content": "GC pause\n 1200 ms"},
]
DAVIS_EVENTS = [
    {
        "event.id": "8302664511948770210_1791814320000",
        "event.name": "Out-of-memory kills",
        "event.type": "ERROR_EVENT",
        "event.start": "2026-10-12T14:11:00.000000000Z",
        "dt.smartscape_source.id": "PROCESS_GROUP_INSTANCE-6A1E3F9C2B7D4E11",
        "dt.smartscape_source.type": "PROCESS_GROUP_INSTANCE",
    }
]


def event(**kw: Any) -> ProblemEvent:
    return ProblemEvent.model_validate(
        {
            "source": AlertSource.DYNATRACE_WORKFLOW,
            "problem_id": PROBLEM_ID,
            "display_id": "P-unknown",
            "status": ProblemStatus.ACTIVE,
            "title": "Out of memory",
            "started_at": NOW,
            "observed_at": NOW,
        }
        | kw
    )


class InboxStub:
    def __init__(self, events: list[ProblemEvent]) -> None:
        self._events = {e.problem_id: e for e in events}

    async def latest_event(self, problem_id: str) -> ProblemEvent | None:
        return self._events.get(problem_id)


def grail_handler(request: httpx2.Request) -> httpx2.Response:
    if request.url.host == "sso.dynatrace.com":
        return httpx2.Response(200, json={"access_token": "tok", "expires_in": 300})
    dql = json.loads(request.content)["query"]
    if dql.startswith("fetch logs"):
        records: list[dict[str, Any]] = LOG_RECORDS
    elif dql.startswith("fetch dt.davis.problems"):
        records = [GRAIL_PROBLEM] if json.dumps(PROBLEM_ID) in dql else []
    elif dql.startswith("fetch dt.davis.events"):
        records = DAVIS_EVENTS
    else:
        return httpx2.Response(400, json={"error": "unexpected query"})
    return httpx2.Response(200, json={"state": "SUCCEEDED", "result": {"records": records}})


@dataclass
class Subject:
    source: ProblemContextSource
    close: Callable[[], Awaitable[None]]


async def _noop() -> None:
    return None


def fake_subject(max_log_lines: int) -> Subject:
    inbox = InboxStub([event()])
    return Subject(FakeProblemContextSource(CONTEXT, inbox, max_log_lines), _noop)  # type: ignore[arg-type]


def grail_subject(max_log_lines: int) -> Subject:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(grail_handler))
    client = GrailClient(http, DynatraceOAuth(http, GRAIL), GRAIL, poll_interval_s=0)
    return Subject(GrailProblemContextSource(client, max_log_lines=max_log_lines), http.aclose)


@pytest.fixture(params=[fake_subject, grail_subject], ids=["fake", "grail"])
def make(request: pytest.FixtureRequest) -> Callable[[int], Subject]:
    return request.param  # type: ignore[no-any-return]


@pytest.mark.parametrize("target", [TARGET, None], ids=["target", "no-target"])
async def test_context_shape(make: Callable[[int], Subject], target: TargetSummary | None) -> None:
    subject = make(50)
    resolved = ResolveTargetResult(target=target, recent_restarts_24h=2)
    ev = event()
    try:
        ctx = await subject.source.get_context(ev, resolved)
    finally:
        await subject.close()
    assert ctx.event == ev and ctx.target == target and ctx.recent_restarts_24h == 2
    assert all(isinstance(line, str) and line for line in ctx.logs)
    assert len(ctx.logs) == len(set(ctx.logs)) <= 50


async def test_log_lines_are_capped(make: Callable[[int], Subject]) -> None:
    subject = make(1)
    try:
        ctx = await subject.source.get_context(event(), ResolveTargetResult(target=TARGET))
    finally:
        await subject.close()
    assert len(ctx.logs) == 1


async def test_get_problem_known_and_unknown(make: Callable[[int], Subject]) -> None:
    subject = make(50)
    try:
        known = await subject.source.get_problem(PROBLEM_ID)
        unknown = await subject.source.get_problem("-1_0V2")
    finally:
        await subject.close()
    assert known is not None and known.problem_id == PROBLEM_ID and known.status is ProblemStatus.ACTIVE
    assert unknown is None


# ------------------------------------------------------------------------------- Grail adapter specifics
def source(handler: Any, seen: list[str] | None = None, timeout: float = 5.0) -> GrailProblemContextSource:
    def wrapped(request: httpx2.Request) -> httpx2.Response:
        if seen is not None and request.url.host != "sso.dynatrace.com":
            seen.append(json.loads(request.content)["query"])
        return handler(request)  # type: ignore[no-any-return]

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(wrapped))
    client = GrailClient(http, DynatraceOAuth(http, GRAIL), GRAIL, poll_interval_s=0)
    return GrailProblemContextSource(client, max_log_lines=50, timeout_seconds=timeout)


def test_logs_dql_is_scoped_by_target_and_validated() -> None:
    dql = logs_dql(TARGET, None, 30, 200)
    assert dql is not None
    assert 'container.name == "kafka"' in dql and '{"HOST-91C2D4E5F6A70811"}' in dql
    assert "from: now() - 30m" in dql and "| limit 200" in dql
    unsafe = TARGET.model_copy(update={"container": 'kafka" or true or "'})
    assert logs_dql(unsafe, None, 30, 10) is None  # nothing safe to scope to: no unscoped scan
    by_root = logs_dql(None, "PROCESS_GROUP_INSTANCE-1", 30, 10)
    assert by_root is not None and 'iAny(dt.source_entity[] == "PROCESS_GROUP_INSTANCE-1")' in by_root
    assert logs_dql(None, None, 30, 10) is None
    with pytest.raises(ValueError, match="not safe"):
        problem_dql('x" or true', 30)


def test_log_lines_and_template_dedupe() -> None:
    line = log_line({"timestamp": "2026-10-12T14:11:09.123456789Z", "status": "ERROR", "content": "a\n  b"})
    assert line == "2026-10-12T14:11:09Z ERROR a b"
    lines = ["t1 ERROR retry 1 of 5", "t2 ERROR retry 2 of 5", "t3 WARN other"]
    assert dedupe_by_template(lines, 50) == ("t1 ERROR retry 1 of 5", "t3 WARN other")


async def test_context_maps_logs_and_evidence() -> None:
    seen: list[str] = []
    src = source(grail_handler, seen)
    ctx = await src.get_context(event(), ResolveTargetResult(target=TARGET))
    # newest first; "heap 4096" and "heap 2048" share a template
    assert ctx.logs[0] == "2026-10-12T14:11:09Z ERROR OutOfMemoryError: heap 4096"
    assert len(ctx.logs) == 3 and ctx.logs[-1] == "2026-10-12T14:11:06Z WARN GC pause 1200 ms"
    [ev] = ctx.evidence
    assert ev.evidence_type == "EVENT" and ev.event_type == "ERROR_EVENT"
    assert ev.event_id == DAVIS_EVENTS[0]["event.id"]
    assert ev.entity is not None and ev.entity.type == "process_group_instance"
    events_query = next(q for q in seen if q.startswith("fetch dt.davis.events"))
    assert "8302664511948770211_1791814320000" in events_query  # every linked event ID is asked for


@pytest.mark.parametrize(
    "failure",
    [
        lambda r: httpx2.Response(500),
        lambda r: httpx2.Response(200, json={"state": "FAILED"}),
        lambda r: httpx2.Response(200, json={"unexpected": True}),
    ],
    ids=["http-500", "query-failed", "bad-shape"],
)
async def test_grail_failures_degrade_context_and_fail_closed_on_problem(failure: Any) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "sso.dynatrace.com":
            return grail_handler(request)
        return failure(request)  # type: ignore[no-any-return]

    src = source(handler)
    ev = event()
    ctx = await src.get_context(ev, ResolveTargetResult(target=TARGET))
    assert ctx.event == ev and ctx.logs == () and ctx.evidence == ()
    assert await src.get_problem(PROBLEM_ID) is None  # the guard then skips the restart


async def test_sso_failure_degrades() -> None:
    src = source(lambda r: httpx2.Response(401))
    ctx = await src.get_context(event(), ResolveTargetResult(target=None))
    assert ctx.logs == () and ctx.evidence == ()


async def test_unsafe_problem_id_is_not_queried() -> None:
    seen: list[str] = []
    src = source(grail_handler, seen)
    assert await src.get_problem('x" or true or "') is None
    assert seen == []
