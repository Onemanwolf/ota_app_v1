"""Azure Communication Services Email over REST (OTA-AZ-001 Section 7.8, ADR 0013).

Plain httpx2 instead of the azure-communication-email SDK: no extra dependency, and the token comes from the
process's one `TokenProvider` (the app's managed identity), like every other cloud adapter.

Send-and-accept, no polling: ACS answers `202 Accepted` with an `Operation-Location` to poll for the final
delivery status. Waiting for it can take minutes and does not change what the caller can do, so a 202 counts
as delivered to ACS; the operation id is logged for tracing. Bounces are visible in the ACS resource logs.
"""

from __future__ import annotations

import re
import uuid
from email.utils import formatdate
from typing import Any

import httpx2
import structlog
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter
from tenacity.wait import WaitBaseT

from opstriage_notification.domain.email import OutboundEmail
from opstriage_notification.ports import ChannelDeliveryError
from opstriage_shared.credentials import SCOPE_COMMUNICATION, TokenProvider

log = structlog.get_logger()

DEFAULT_API_VERSION = "2023-03-31"  # the GA Email REST API version
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_ERROR_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx2.TransportError) or (
        isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code in _RETRYABLE_STATUS
    )


def _error_code(resp: httpx2.Response) -> str | None:
    """ACS error responses are `{"error": {"code": ..., "message": ...}}`. Only the code is kept: the
    message can echo addresses. INFERRED: the code is a short identifier (e.g. `InvalidSenderAddress`)."""
    try:
        code = resp.json().get("error", {}).get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) and _ERROR_CODE.match(code) else None


class AcsEmailSender:
    def __init__(
        self,
        http: httpx2.AsyncClient,
        endpoint: str,
        credentials: TokenProvider,
        *,
        api_version: str = DEFAULT_API_VERSION,
        attempts: int = 5,
        wait: WaitBaseT | None = None,
    ) -> None:
        self._http, self._credentials = http, credentials
        self._url = f"{endpoint.rstrip('/')}/emails:send"
        self._api_version, self._attempts = api_version, attempts
        self._wait = wait or wait_exponential_jitter(multiplier=1, max=20)

    async def send(self, mail: OutboundEmail) -> None:
        content: dict[str, Any] = {"subject": mail.subject, "plainText": mail.text}
        if mail.html:
            content["html"] = mail.html
        body = {
            "senderAddress": mail.sender,  # DoNotReply@<verified domain>
            "recipients": {"to": [{"address": a} for a in mail.to]},
            "content": content,
        }
        # One id per logical send, reused on every retry, so ACS can drop a duplicate of a request whose
        # 202 was lost in transit. INFERRED from the ACS REST reference: `Operation-Id` names the operation
        # (and becomes the id in Operation-Location); `repeatability-request-id` + `repeatability-first-sent`
        # (RFC 1123 date of the first attempt) make retries idempotent.
        operation_id = str(uuid.uuid4())
        idempotency = {
            "Operation-Id": operation_id,
            "repeatability-request-id": operation_id,
            "repeatability-first-sent": formatdate(usegmt=True),
        }
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_retryable),
                reraise=True,
                wait=self._wait,
                stop=stop_after_attempt(self._attempts),
            ):
                with attempt:
                    resp = await self._post(body, idempotency)
        except httpx2.HTTPStatusError as exc:
            code = _error_code(exc.response)
            log.warning(
                "acs_email_rejected",
                status=exc.response.status_code,
                error_code=code,
                operation_id=operation_id,
            )
            raise ChannelDeliveryError("email", f"HTTP {exc.response.status_code}") from None
        except httpx2.HTTPError as exc:
            raise ChannelDeliveryError("email", type(exc).__name__) from None
        log.info(
            "acs_email_accepted", status=resp.status_code, operation_id=operation_id, recipients=len(mail.to)
        )

    async def _post(self, body: dict[str, Any], idempotency: dict[str, str]) -> httpx2.Response:
        try:
            # Asked per attempt: the provider caches and refreshes tokens, so a retry never sends a stale one.
            token = await self._credentials.token(SCOPE_COMMUNICATION)
        except Exception as exc:  # azure-identity raises its own hierarchy; never surface its message
            raise ChannelDeliveryError("email", f"credentials: {type(exc).__name__}") from None
        resp = await self._http.post(
            self._url,
            params={"api-version": self._api_version},
            json=body,
            headers={"Authorization": f"Bearer {token}", **idempotency},
            timeout=15,
        )
        resp.raise_for_status()
        return resp
