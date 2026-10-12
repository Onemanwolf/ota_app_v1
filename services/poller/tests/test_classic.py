import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
from pydantic import SecretStr

from opstriage_contracts.problems import AlertSource, ProblemStatus
from opstriage_poller.adapters.classic import ClassicProblemSource
from opstriage_poller.settings import ClassicSettings

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dynatrace" / "problems_v2" / "list_page_1.json"
NOW = datetime(2026, 10, 12, 12, 30, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


async def test_pagination_mapping_and_auth() -> None:
    page1 = json.loads(FIXTURE.read_text())
    page2 = {
        "totalCount": 2,
        "pageSize": 1,
        "problems": [
            page1["problems"][0]
            | {"problemId": "p-2", "displayId": "P-2", "status": "CLOSED", "endTime": 1760271000000}
        ],
    }
    seen: list[httpx2.Request] = []

    def handler(r: httpx2.Request) -> httpx2.Response:
        seen.append(r)
        return httpx2.Response(200, json=page2 if "nextPageKey" in r.url.params else page1)

    settings = ClassicSettings(environment_url="https://abc.live.dynatrace.com", api_token=SecretStr("t0k"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        source = ClassicProblemSource(http, settings, timedelta(hours=7), Clock())
        events = await source.fetch_changed(NOW - timedelta(minutes=5), timedelta(hours=7))

    assert [e.status for e in events] == [ProblemStatus.ACTIVE, ProblemStatus.CLOSED]
    assert events[0].source is AlertSource.DYNATRACE_POLLER_CLASSIC
    assert events[0].ended_at is None and events[1].ended_at is not None  # endTime -1 while open
    assert events[0].root_cause_entity is not None and events[0].root_cause_entity.type == "container"
    assert seen[0].headers["Authorization"] == "Api-Token t0k"
    assert seen[0].url.path == "/api/v2/problems" and seen[0].url.params["pageSize"] == "500"
    # with nextPageKey, no other query parameters are sent
    assert dict(seen[1].url.params) == {"nextPageKey": "___next_page_key_2___"}
