"""EmailSender port contract, run against every adapter that can be built without a network (ADR 0013)."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from structlog.testing import capture_logs
from tenacity import wait_none

from opstriage_notification.adapters.acs_email import AcsEmailSender
from opstriage_notification.adapters.log_senders import LogEmailSender
from opstriage_notification.domain.email import OutboundEmail
from opstriage_notification.ports import ChannelDeliveryError, EmailSender
from opstriage_shared.credentials import SCOPE_COMMUNICATION, StaticCredentials

TOKEN = "tok-SECRET-123"
ENDPOINT = "https://acs-test.communication.azure.com/"
MAIL = OutboundEmail(
    sender="DoNotReply@example.test",
    to=("oncall@example.test", "lead@example.test"),
    subject="[CRITICAL] Container down",
    text="BODY-TEXT-sensitive",
    html="<p>BODY-HTML-sensitive</p>",
)

Handler = Callable[[httpx2.Request], httpx2.Response]


class AcsHarness:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses, self.requests = list(statuses), list[httpx2.Request]()
        self.creds = StaticCredentials(TOKEN)

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if status == 202:
            return httpx2.Response(202, headers={"operation-location": f"{ENDPOINT}emails/operations/x"})
        return httpx2.Response(status, json={"error": {"code": "BadThing", "message": f"to {MAIL.to[0]}"}})


@pytest.fixture
async def http_factory() -> AsyncIterator[Callable[[Handler], httpx2.AsyncClient]]:
    clients: list[httpx2.AsyncClient] = []

    def make(handler: Handler) -> httpx2.AsyncClient:
        clients.append(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        return clients[-1]

    yield make
    for c in clients:
        await c.aclose()


def acs_sender(harness: AcsHarness, http: httpx2.AsyncClient) -> AcsEmailSender:
    return AcsEmailSender(http, ENDPOINT, harness.creds, attempts=3, wait=wait_none())


def _sender(kind: str, http_factory: Callable[[Handler], httpx2.AsyncClient]) -> EmailSender:
    if kind == "log":
        return LogEmailSender()
    harness = AcsHarness([202])
    return acs_sender(harness, http_factory(harness.handler))


def _assert_no_leaks(logs: list[dict[str, Any]]) -> None:
    dumped = json.dumps(logs, default=str)
    for secret in (TOKEN, MAIL.text, MAIL.html, *MAIL.to):
        assert secret not in dumped


# ---- contract: every EmailSender ------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["log", "acs"])
async def test_contract_send_succeeds_without_leaking(
    kind: str, http_factory: Callable[[Handler], httpx2.AsyncClient]
) -> None:
    sender = _sender(kind, http_factory)
    with capture_logs() as logs:
        await sender.send(MAIL)  # success: returns None, raises nothing
    _assert_no_leaks(logs)


# ---- ACS adapter specifics ------------------------------------------------------------------------------


async def test_acs_request_shape(http_factory: Callable[[Handler], httpx2.AsyncClient]) -> None:
    h = AcsHarness([202])
    await acs_sender(h, http_factory(h.handler)).send(MAIL)

    (req,) = h.requests
    assert req.method == "POST"
    assert str(req.url).startswith("https://acs-test.communication.azure.com/emails:send?")
    assert req.url.params["api-version"] == "2023-03-31"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["operation-id"] and req.headers["repeatability-request-id"]
    assert req.headers["repeatability-first-sent"].endswith("GMT")
    assert json.loads(req.content) == {
        "senderAddress": "DoNotReply@example.test",
        "recipients": {"to": [{"address": "oncall@example.test"}, {"address": "lead@example.test"}]},
        "content": {"subject": MAIL.subject, "plainText": MAIL.text, "html": MAIL.html},
    }
    assert h.creds.scopes == [SCOPE_COMMUNICATION]


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_acs_retries_transient_then_succeeds(
    status: int, http_factory: Callable[[Handler], httpx2.AsyncClient]
) -> None:
    h = AcsHarness([status, status, 202])
    await acs_sender(h, http_factory(h.handler)).send(MAIL)
    assert len(h.requests) == 3
    # Retries are the same logical operation: ACS can deduplicate them.
    assert len({r.headers["repeatability-request-id"] for r in h.requests}) == 1
    assert len({r.headers["repeatability-first-sent"] for r in h.requests}) == 1


@pytest.mark.parametrize("status", [429, 502])
async def test_acs_transient_failure_surfaces_after_retries(
    status: int, http_factory: Callable[[Handler], httpx2.AsyncClient]
) -> None:
    h = AcsHarness([status])
    with capture_logs() as logs, pytest.raises(ChannelDeliveryError) as info:
        await acs_sender(h, http_factory(h.handler)).send(MAIL)
    assert len(h.requests) == 3
    assert info.value.channel == "email" and str(status) in info.value.detail
    assert info.value.__cause__ is None
    _assert_no_leaks(logs)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_acs_client_error_is_permanent(
    status: int, http_factory: Callable[[Handler], httpx2.AsyncClient]
) -> None:
    h = AcsHarness([status])
    with capture_logs() as logs, pytest.raises(ChannelDeliveryError) as info:
        await acs_sender(h, http_factory(h.handler)).send(MAIL)
    assert len(h.requests) == 1
    assert TOKEN not in str(info.value) and MAIL.to[0] not in str(info.value)
    assert any(e.get("error_code") == "BadThing" for e in logs)
    _assert_no_leaks(logs)


async def test_acs_transport_error_is_retried_then_surfaced(
    http_factory: Callable[[Handler], httpx2.AsyncClient],
) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        raise httpx2.ConnectError("boom", request=request)

    sender = AcsEmailSender(
        http_factory(handler), ENDPOINT, StaticCredentials(TOKEN), attempts=3, wait=wait_none()
    )
    with pytest.raises(ChannelDeliveryError) as info:
        await sender.send(MAIL)
    assert calls == 3 and info.value.detail == "ConnectError"


async def test_acs_credential_failure_is_a_delivery_error_without_detail(
    http_factory: Callable[[Handler], httpx2.AsyncClient],
) -> None:
    class Broken:
        async def token(self, scope: str) -> str:
            raise RuntimeError(f"cannot reach IMDS for {TOKEN}")

        async def close(self) -> None:
            return None

    h = AcsHarness([202])
    sender = AcsEmailSender(http_factory(h.handler), ENDPOINT, Broken(), attempts=3, wait=wait_none())
    with pytest.raises(ChannelDeliveryError) as info:
        await sender.send(MAIL)
    assert h.requests == [] and TOKEN not in str(info.value)
