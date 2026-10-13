"""AzureRunCommandExecutor: VM Run Command over the ARM REST API (spec 12.5.2, OTA-AZ-001 6.1 and 7.9).

REST with httpx2 instead of azure-mgmt-compute (deviation from the spec sample, ADR 0013): one HTTP client, an
injectable transport for the contract test, and no Azure SDK in the local image. The ARM token comes from the
process-wide `TokenProvider` (scope `SCOPE_ARM`); azure-identity caches and refreshes it.

Safety properties:
- The script is a constant. The only variable inputs are the container name (from the target registry, never
  from a request or model output) and the timeout, both validated here and passed as Run Command *parameters*,
  never interpolated into shell text. The VM-side `restart-allowed.sh` validates them again and is the real
  allow-list: the Run Command role is root on the VM regardless of script content.
- Run Command cannot be cancelled. On timeout the executor says so: the restart may still complete on the VM.
- Tokens, request bodies and full ARM payloads are never logged or returned; output is cut to the last
  4,096 bytes.

ARM facts marked INFERRED below come from the public REST reference and SDK behaviour, not yet confirmed
against a test VM (spec 12.5.5 DoD: "One Azure test VM restarted through azure_run_command").
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx2
import structlog
from tenacity import AsyncRetrying, RetryCallState, retry_if_exception, stop_after_attempt

from opstriage_remediation.domain.targets import RestartTarget
from opstriage_remediation.ports.executor import ContainerStatus, ExecutionOutcome, ExecutorUnavailable
from opstriage_remediation.settings import AzureSettings
from opstriage_shared.credentials import SCOPE_ARM, TokenProvider

log = structlog.get_logger()

# Same rule as the registry (TARGET_ID_PATTERN) and restart-allowed.sh. fullmatch + ASCII: no trailing "\n".
CONTAINER_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}", re.ASCII)
_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
VM_RESOURCE_ID = re.compile(
    rf"/subscriptions/(?P<sub>{_GUID})/resourceGroups/[A-Za-z0-9._()-]{{1,90}}"
    r"/providers/Microsoft\.Compute/virtualMachines/[A-Za-z0-9._-]{1,64}",
    re.ASCII,
)
OUTPUT_LIMIT = 4096  # Run Command keeps only the last 4,096 bytes of output anyway
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_TERMINAL = {"succeeded", "failed", "canceled"}
_EXIT_CODE = re.compile(r"exit_code=(\d+)")


def build_script(allowlist_script: str) -> list[str]:
    """Constant script. `$1`/`$2` are the Run Command parameters arg1/arg2 (positional for Linux
    RunShellScript, as with `az vm run-command invoke --parameters`; INFERRED, confirm on the test VM)."""
    return [f'{allowlist_script} "$1" "$2"']


class _RetryableHttp(Exception):
    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.retry_after = status, retry_after


@dataclass(frozen=True, slots=True)
class _Poll:
    url: str
    wait_s: float


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After in seconds or as an HTTP date. Bad values are ignored (the default interval applies)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _arm_error_code(resp: httpx2.Response) -> str:
    """The ARM error *code* only (`{"error": {"code": ...}}`); messages can echo request details."""
    try:
        body = resp.json()
    except ValueError:
        return "unknown"
    code = body.get("error", {}).get("code") if isinstance(body, dict) else None
    return re.sub(r"[^A-Za-z0-9_.]", "", str(code))[:64] if code else "unknown"


def _json(resp: httpx2.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _messages(body: Any) -> list[str] | None:
    """Instance-view statuses of the run: `{"value": [...]}` (Location result) or the same list under
    `properties.output` (Azure-AsyncOperation result). INFERRED: both shapes, as the SDK accepts either."""
    if not isinstance(body, dict):
        return None
    value = body.get("value")
    if value is None:
        output = (body.get("properties") or {}).get("output")
        value = output.get("value") if isinstance(output, dict) else None
    if not isinstance(value, list):
        return None
    return [str(m.get("message") or "") for m in value if isinstance(m, dict)]


def interpret_output(messages: list[str]) -> ExecutionOutcome:
    """Linux messages look like 'Enable succeeded: \\n[stdout]\\n...\\n[stderr]\\n...' (INFERRED).
    restart-allowed.sh prints `exit_code=N` last on stdout; only `exit_code=0` there is success."""
    text = "\n".join(messages)
    stdout = text.split("[stdout]", 1)[1] if "[stdout]" in text else text
    stdout = stdout.split("[stderr]", 1)[0]
    codes = _EXIT_CODE.findall(stdout)
    succeeded = bool(codes) and codes[-1] == "0"
    return ExecutionOutcome(
        succeeded=succeeded, output_tail=text.encode()[-OUTPUT_LIMIT:].decode(errors="replace")
    )


class AzureRunCommandExecutor:
    kind = "azure_run_command"

    def __init__(
        self,
        http: httpx2.AsyncClient,
        credentials: TokenProvider,
        settings: AzureSettings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        owns_http: bool = False,
    ) -> None:
        self._http, self._creds, self._s = http, credentials, settings
        self._sleep, self._clock, self._owns_http = sleep, clock, owns_http
        self._script = build_script(settings.allowlist_script)

    @classmethod
    def create(cls, credentials: TokenProvider, settings: AzureSettings) -> AzureRunCommandExecutor:
        http = httpx2.AsyncClient(timeout=settings.http_timeout_seconds, follow_redirects=False)
        return cls(http, credentials, settings, owns_http=True)

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ---- validation (before any HTTP call) ----

    def _validate(self, target: RestartTarget, timeout_s: int) -> str:
        container, resource_id = target.executor.container, target.executor.resource_id
        if not CONTAINER_NAME.fullmatch(container):
            raise ExecutorUnavailable("invalid container name in target registry")
        if not 1 <= timeout_s <= 300:
            raise ExecutorUnavailable("invalid restart timeout")
        match = VM_RESOURCE_ID.fullmatch(resource_id or "")
        if match is None:
            raise ExecutorUnavailable(f"target {target.id} has no valid Azure VM resource_id")
        sub = self._s.subscription_id
        if sub and match["sub"].lower() != sub.lower():
            raise ExecutorUnavailable(f"target {target.id} is outside REMEDIATION__AZURE__SUBSCRIPTION_ID")
        return match.group(0)

    # ---- port ----

    async def restart(self, target: RestartTarget, timeout_s: int) -> ExecutionOutcome:
        resource_id = self._validate(target, timeout_s)  # raises before any token or HTTP call
        deadline = self._clock() + timeout_s + self._s.run_command_overhead_seconds
        url = f"{self._s.arm_endpoint}{resource_id}/runCommand"
        body = {
            "commandId": "RunShellScript",
            "script": self._script,
            "parameters": [
                {"name": "arg1", "value": target.executor.container},
                {"name": "arg2", "value": str(timeout_s)},
            ],
        }
        log.info("run_command_started", target_id=target.id, executor=self.kind)
        resp = await self._call(
            "POST", url, json=body, params={"api-version": self._s.run_command_api_version}
        )
        if resp.status_code == 200:  # finished synchronously (INFERRED: ARM normally answers 202)
            return self._finish(target, _json(resp))
        if resp.status_code != 202:
            raise ExecutorUnavailable(f"unexpected ARM status {resp.status_code}")
        # INFERRED: runCommand's final state is via Azure-AsyncOperation; Location returns the result body.
        status_url = resp.headers.get("Azure-AsyncOperation") or resp.headers.get("Location")
        result_url = resp.headers.get("Location")
        if not status_url:
            raise ExecutorUnavailable("ARM accepted the run command without a status URL")
        for u in (status_url, result_url):
            if u and not self._is_arm(u):  # the bearer token only ever goes to the ARM endpoint
                raise ExecutorUnavailable("ARM returned a status URL outside the ARM endpoint")
        poll = _Poll(status_url, self._wait(resp))
        while True:
            if self._clock() + poll.wait_s > deadline:
                log.warning("run_command_timed_out", target_id=target.id)
                raise ExecutorUnavailable("run command timed out; the script may still complete on the VM")
            await self._sleep(poll.wait_s)
            resp = await self._call("GET", poll.url)
            if resp.status_code == 202:  # Location polling: still running
                poll = _Poll(poll.url, self._wait(resp))
                continue
            payload = _json(resp)
            status = str(payload.get("status", "")).lower() if isinstance(payload, dict) else ""
            if status and status not in _TERMINAL:
                poll = _Poll(poll.url, self._wait(resp))
                continue
            if status in {"failed", "canceled"} and _messages(payload) is None:
                raw = str((payload.get("error") or {}).get("code") or "unknown")
                code = re.sub(r"[^A-Za-z0-9_.]", "", raw)[:64] or "unknown"
                log.warning("run_command_failed", target_id=target.id, arm_status=status, error_code=code)
                return ExecutionOutcome(succeeded=False, output_tail=f"arm_status={status} error_code={code}")
            if _messages(payload) is None and result_url and result_url != poll.url:
                payload = _json(await self._call("GET", result_url))
            return self._finish(target, payload)

    async def status(self, target: RestartTarget) -> ContainerStatus:
        # Reading container state would need another Run Command (>= 20 s, one at a time per VM, root) and the
        # allow-list script only restarts. Not offered: the use case reports it as unavailable.
        raise ExecutorUnavailable("container status is not observable through azure_run_command")

    # ---- helpers ----

    def _finish(self, target: RestartTarget, payload: Any) -> ExecutionOutcome:
        messages = _messages(payload)
        if messages is None:
            raise ExecutorUnavailable("ARM run command result had no output")
        outcome = interpret_output(messages)
        log.info("run_command_finished", target_id=target.id, succeeded=outcome.succeeded)
        return outcome

    def _is_arm(self, url: str) -> bool:
        return url.startswith(f"{self._s.arm_endpoint}/")

    def _wait(self, resp: httpx2.Response) -> float:
        after = parse_retry_after(resp.headers.get("Retry-After"))
        return min(after if after is not None else self._s.poll_interval_seconds, 60.0)

    async def _call(self, method: str, url: str, **kwargs: Any) -> httpx2.Response:
        """One ARM call with retries on 429, 5xx and transport errors (Retry-After honoured). Other 4xx are
        permanent. Errors carry the status and ARM error code only: never the token, URL query or body."""

        def wait(state: RetryCallState) -> float:
            exc = state.outcome.exception() if state.outcome else None
            if isinstance(exc, _RetryableHttp) and exc.retry_after is not None:
                return min(exc.retry_after, 60.0)
            return min(2.0 ** (state.attempt_number - 1), 30.0)

        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(lambda e: isinstance(e, _RetryableHttp | httpx2.TransportError)),
                stop=stop_after_attempt(self._s.max_attempts),
                wait=wait,
                sleep=self._sleep,
                reraise=True,
            ):
                with attempt:
                    token = await self._creds.token(SCOPE_ARM)
                    resp = await self._http.request(
                        method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs
                    )
                    if resp.status_code in _RETRYABLE_STATUS:
                        raise _RetryableHttp(
                            resp.status_code, parse_retry_after(resp.headers.get("Retry-After"))
                        )
                    if resp.status_code >= 400:
                        code = _arm_error_code(resp)
                        log.warning("arm_request_rejected", status=resp.status_code, error_code=code)
                        raise ExecutorUnavailable(f"ARM rejected the request: HTTP {resp.status_code} {code}")
                    return resp
        except _RetryableHttp as exc:
            raise ExecutorUnavailable(f"ARM unavailable after retries: {exc}") from None
        except httpx2.TransportError as exc:
            raise ExecutorUnavailable(f"ARM unreachable: {type(exc).__name__}") from None
        raise AssertionError("unreachable")  # pragma: no cover
