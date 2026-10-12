import json
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from opstriage_contracts.tools import NotificationKind, NotificationRequest, Severity
from opstriage_notification.adapters.deliveries import InMemoryDeliveryStore
from opstriage_notification.adapters.teams_workflows import WorkflowsWebhookTeamsSender
from opstriage_notification.application.send_notification import SendNotification
from opstriage_notification.domain.email import OutboundEmail
from opstriage_notification.domain.rate_limit import TokenBucketLimiter
from opstriage_notification.domain.teams_card import build_teams_message
from opstriage_notification.ports import ChannelDeliveryError


class RecordingTeams:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail = fail

    async def send(self, message: dict[str, Any]) -> None:
        if self.fail:
            raise ChannelDeliveryError("teams", "HTTPStatusError")
        self.sent.append(message)


class RecordingEmail:
    def __init__(self) -> None:
        self.sent: list[OutboundEmail] = []

    async def send(self, mail: OutboundEmail) -> None:
        self.sent.append(mail)


def request(key: str = "run-1:notify:opened", **kw: Any) -> NotificationRequest:
    return NotificationRequest.model_validate(
        {
            "idempotency_key": key,
            "kind": NotificationKind.PROBLEM_OPENED,
            "severity": Severity.CRITICAL,
            "title": "[P-1] Container down",
            "summary": "Container demo-orders-api is unavailable.",
            "facts": {"Action": "restart_container"},
            "links": {"Problem": "https://example/p"},
        }
        | kw
    )


def build(teams: RecordingTeams, email: RecordingEmail, per_minute: int = 20) -> SendNotification:
    return SendNotification(
        teams,
        email,
        InMemoryDeliveryStore(),
        TokenBucketLimiter(per_minute, lambda: 0.0),
        card_version="1.5",
        sender="opstriage@local.test",
        default_recipients=("oncall@x",),
    )


async def test_sends_to_both_channels_once_per_key() -> None:
    teams, email = RecordingTeams(), RecordingEmail()
    send = build(teams, email)
    first = await send(request())
    second = await send(request())
    assert [o.delivered for o in first.outcomes] == [True, True] and not first.duplicate
    assert second.duplicate and len(teams.sent) == 1 and len(email.sent) == 1
    assert email.sent[0].to == ("oncall@x",) and email.sent[0].subject.startswith("[CRITICAL]")


async def test_one_failing_channel_does_not_block_the_other() -> None:
    email = RecordingEmail()
    result = await build(RecordingTeams(fail=True), email)(request())
    assert {o.channel: o.delivered for o in result.outcomes} == {"teams": False, "email": True}


async def test_rate_limit_is_per_channel() -> None:
    teams, email = RecordingTeams(), RecordingEmail()
    send = build(teams, email, per_minute=1)
    await send(request("run-1:notify:a"))
    limited = await send(request("run-1:notify:b"))
    assert all(o.detail == "rate limited" for o in limited.outcomes)


def test_card_is_adaptive_card_and_small() -> None:
    message = build_teams_message(request(summary="x" * 4000))
    card = message["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.5"
    assert card["body"][0]["color"] == "Attention"
    assert len(json.dumps(message).encode()) < 28_000


async def test_workflows_sender_hides_url_in_errors() -> None:
    url = "https://prod.example/workflows/abc?sig=SECRET"

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        sender = WorkflowsWebhookTeamsSender(http, SecretStr(url))
        with pytest.raises(ChannelDeliveryError) as info:
            await sender.send({"type": "message"})
    assert "SECRET" not in str(info.value) and info.value.__cause__ is None
