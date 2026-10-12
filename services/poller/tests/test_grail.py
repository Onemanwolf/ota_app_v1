import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from opstriage_contracts.problems import AlertSource, ProblemStatus
from opstriage_poller.adapters.grail import (
    DynatraceOAuth,
    GrailClient,
    GrailProblemSource,
    GrailQueryError,
    map_record,
    render_active,
    render_changed,
)
from opstriage_poller.settings import GrailSettings

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "dynatrace" / "grail"
SETTINGS = GrailSettings(
    environment_url="https://abc123.apps.dynatrace.com",
    oauth_client_id="id",
    oauth_client_secret=SecretStr("secret"),
)
RECORD = {
    "event.id": "e-1",
    "status": "ACTIVE",
    "name": "down",
    "display_id": "P-1",
    "category": "AVAILABILITY",
    "severity": 4,
    "start": "2026-10-12T10:00:00Z",
    "end": None,
    "affected": [],
    "root_cause": None,
    "maintenance": False,
    "last_update": "2026-10-12T10:01:00Z",
}


def test_mapper_against_recorded_batch() -> None:
    records = json.loads((FIXTURES / "problems_batch_01.json").read_text())
    events = [map_record(r) for r in records]
    assert [e.display_id for e in events] == ["P-2610130", "P-2610131"]
    assert all(e.source is AlertSource.DYNATRACE_POLLER_GRAIL for e in events)
    assert events[0].root_cause_entity is not None and events[0].root_cause_entity.name == "demo-orders-api"
    # ID-only entity arrays are tolerated
    assert events[1].affected_entities[0].id == "SERVICE-5C1E2A3B4D5F6071"
    assert events[1].affected_entities[0].type == "service"


def test_maintenance_becomes_a_tag() -> None:
    assert map_record(RECORD | {"maintenance": True}).tags == {"maintenance": "true"}


def test_dql_rendering() -> None:
    since = datetime(2026, 10, 12, 10, 0, 0, tzinfo=UTC)
    dql = render_changed(since, timedelta(hours=7))
    assert "from: now() - 7h" in dql and 'toTimestamp("2026-10-12T10:00:00Z")' in dql
    assert "${" not in dql and "${" not in render_active(timedelta(hours=7))
    assert 'status == "ACTIVE"' in render_active(timedelta(hours=7))


class FakeTime:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def transport(handler: Any, calls: list[httpx2.Request]) -> httpx2.MockTransport:
    def wrapped(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        if request.url.host == "sso.dynatrace.com":
            return httpx2.Response(200, json={"access_token": f"tok-{len(calls)}", "expires_in": 300})
        return handler(request)  # type: ignore[no-any-return]

    return httpx2.MockTransport(wrapped)


async def run_query(
    handler: Any, calls: list[httpx2.Request], time: FakeTime | None = None
) -> list[dict[str, Any]]:
    async with httpx2.AsyncClient(transport=transport(handler, calls)) as http:
        oauth = DynatraceOAuth(http, SETTINGS, monotonic=time or FakeTime())
        client = GrailClient(http, oauth, SETTINGS, poll_interval_s=0)
        return await client.query("fetch dt.davis.problems")


async def test_inline_success() -> None:
    calls: list[httpx2.Request] = []
    records = await run_query(
        lambda r: httpx2.Response(200, json={"state": "SUCCEEDED", "result": {"records": [RECORD]}}), calls
    )
    assert records == [RECORD]
    execute = calls[1]
    assert execute.url.path == "/platform/storage/query/v1/query:execute"
    assert execute.headers["Authorization"] == "Bearer tok-1"
    assert json.loads(execute.content)["query"] == "fetch dt.davis.problems"


async def test_polling_path_keeps_the_first_request_token() -> None:
    calls: list[httpx2.Request] = []
    polls = iter([{"state": "RUNNING"}, {"state": "SUCCEEDED", "result": {"records": [RECORD]}}])

    def handler(r: httpx2.Request) -> httpx2.Response:
        if r.url.path.endswith("query:execute"):
            return httpx2.Response(202, json={"state": "RUNNING", "requestToken": "rt-1"})
        assert r.url.params["request-token"] == "rt-1"
        return httpx2.Response(200, json=next(polls))

    assert await run_query(handler, calls) == [RECORD]
    assert sum(r.url.path.endswith("query:poll") for r in calls) == 2


@pytest.mark.parametrize("state", ["FAILED", "RESULT_GONE", "CANCELLED"])
async def test_terminal_failures_raise(state: str) -> None:
    def handler(r: httpx2.Request) -> httpx2.Response:
        if r.url.path.endswith("query:execute"):
            return httpx2.Response(202, json={"state": "RUNNING", "requestToken": "rt"})
        return httpx2.Response(200, json={"state": state})

    with pytest.raises(GrailQueryError, match=state):
        await run_query(handler, [])


async def test_token_is_cached_then_refreshed_before_expiry() -> None:
    calls: list[httpx2.Request] = []
    time = FakeTime()
    ok = httpx2.Response(200, json={"state": "SUCCEEDED", "result": {"records": []}})
    async with httpx2.AsyncClient(transport=transport(lambda r: ok, calls)) as http:
        oauth = DynatraceOAuth(http, SETTINGS, monotonic=time)
        first = await oauth.token()
        time.now += 200
        assert await oauth.token() == first  # still valid
        time.now += 50  # 250 s: inside the 60 s early-refresh window
        assert await oauth.token() != first
    assert sum(r.url.host == "sso.dynatrace.com" for r in calls) == 2
    form = calls[0].content.decode()
    assert "grant_type=client_credentials" in form and "client_secret=secret" in form


async def test_source_maps_query_results() -> None:
    calls: list[httpx2.Request] = []

    def handler(r: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"state": "SUCCEEDED", "result": {"records": [RECORD]}})

    async with httpx2.AsyncClient(transport=transport(handler, calls)) as http:
        source = GrailProblemSource(
            GrailClient(http, DynatraceOAuth(http, SETTINGS), SETTINGS), timedelta(hours=7)
        )
        [event] = await source.fetch_changed(datetime(2026, 10, 12, tzinfo=UTC), timedelta(hours=7))
    assert event.status is ProblemStatus.ACTIVE and event.problem_id == "e-1"
