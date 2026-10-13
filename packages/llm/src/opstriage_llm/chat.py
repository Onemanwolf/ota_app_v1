"""JSON chat against any OpenAI-compatible endpoint, with validation and a repair retry.

Output is untrusted. The caller supplies `validate`, which turns the parsed JSON into a typed value or raises
ValueError (pydantic's ValidationError is a ValueError). On a validation failure the model is shown the error
once (`repair_attempts`) and asked again; after that LlmInvalidOutput is raised. Transport errors, 429 and 5xx
are retried (`attempts`) and then raised as LlmUnavailable. Error messages never contain prompt or output
text.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from opstriage_llm.auth import AuthHeader, auth_for
from opstriage_llm.settings import LlmEndpoint

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LlmUnavailable(RuntimeError):
    """The endpoint could not be reached or kept failing."""


class LlmInvalidOutput(ValueError):
    """The model's output was not valid JSON for the schema, even after repair attempts."""


@dataclass(frozen=True, slots=True)
class ChatResult[T]:
    value: T
    model: str
    latency_ms: int
    calls: int  # 1 + repairs


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx2.TransportError):
        return True
    return isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code in {429, 500, 502, 503, 504}


def parse_json(content: str) -> Any:
    """Parse a JSON reply, tolerating a ```json fence (common in plain-text mode)."""
    m = _FENCE.match(content)
    return json.loads(m.group(1) if m else content)


class ChatJsonClient:
    def __init__(
        self, http: httpx2.AsyncClient, endpoint: LlmEndpoint, auth: AuthHeader | None = None
    ) -> None:
        self._http, self._e = http, endpoint
        self._auth = auth or auth_for(endpoint)
        self._timeout = httpx2.Timeout(endpoint.timeout_seconds, connect=min(3.0, endpoint.timeout_seconds))

    @property
    def endpoint(self) -> LlmEndpoint:
        return self._e

    def request_body(
        self, messages: list[dict[str, str]], schema: dict[str, Any], schema_name: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            **self._e.extra_body,
            "model": self._e.model,
            "temperature": self._e.temperature,
            "max_tokens": self._e.max_tokens,
            "messages": messages,
        }
        if self._e.response_format == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": True},
            }
        elif self._e.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        return body

    def _system(self, system: str, schema: dict[str, Any]) -> str:
        if self._e.response_format == "json_schema":
            return system
        return (
            f"{system}\n\nReply with a single JSON object and nothing else. It must match this JSON Schema:\n"
            f"{json.dumps(schema, separators=(',', ':'))}"
        )

    async def _post(self, body: dict[str, Any]) -> str:
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_retryable),
                reraise=True,
                wait=wait_exponential_jitter(multiplier=0.5, max=5),
                stop=stop_after_attempt(self._e.attempts),
            ):
                with attempt:
                    resp = await self._http.post(
                        self._e.url, json=body, headers=await self._auth(), timeout=self._timeout
                    )
                    resp.raise_for_status()
        except httpx2.HTTPError as exc:
            raise LlmUnavailable(f"llm call failed: {type(exc).__name__}") from exc
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LlmInvalidOutput(f"unexpected response shape: {type(exc).__name__}") from exc
        if not isinstance(content, str):
            raise LlmInvalidOutput("response content is not text")
        return content

    async def complete_json[T](
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        validate: Callable[[Any], T],
        *,
        schema_name: str = "answer",
    ) -> ChatResult[T]:
        started = time.perf_counter()
        messages = [
            {"role": "system", "content": self._system(system, schema)},
            {"role": "user", "content": user},
        ]
        calls = 0
        while True:
            calls += 1
            content = await self._post(self.request_body(messages, schema, schema_name))
            try:
                value = validate(parse_json(content))
            except ValueError as exc:  # JSONDecodeError and pydantic ValidationError are ValueErrors
                if calls > self._e.repair_attempts:
                    raise LlmInvalidOutput(
                        f"invalid output after {calls} call(s): {type(exc).__name__}"
                    ) from exc
                messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "That reply was not valid for the required JSON schema "
                        f"({_brief(exc)}). Reply again with only the corrected JSON object.",
                    },
                ]
                continue
            latency = int((time.perf_counter() - started) * 1000)
            return ChatResult(value, self._e.model, latency, calls)


def _brief(exc: ValueError) -> str:
    """A short, content-free description of what failed (field locations, not values)."""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            locs = [".".join(str(p) for p in e.get("loc", ())) for e in errors()[:5]]
            return "problems at: " + ", ".join(loc or "(root)" for loc in locs)
        except (TypeError, AttributeError):
            pass
    return type(exc).__name__
