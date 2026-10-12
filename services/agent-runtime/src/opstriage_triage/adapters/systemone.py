"""DecisionModel adapter for Kev, Microsoft-Decision-1 (Foundry) and other System One endpoints.

Spec 12.4.3. One adapter serves every provider of the System One decision contract.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx2
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from opstriage_contracts.triage import Action, DecisionSource, TriageDecision
from opstriage_triage.adapters.auth_headers import AuthHeader
from opstriage_triage.domain.questions import QUESTIONS
from opstriage_triage.ports import DecisionInput, DecisionModelError


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx2.TransportError):
        return True
    return isinstance(exc, httpx2.HTTPStatusError) and exc.response.status_code in {429, 500, 502, 503, 504}


class SystemOneDecisionModel:
    def __init__(
        self,
        http: httpx2.AsyncClient,
        *,
        url: str,
        model: str,
        auth_header: AuthHeader,
        timeout_seconds: float,
        shuffle_options: bool = False,
        attempts: int = 3,
    ) -> None:
        self._http, self._url, self._model = http, url, model
        self._auth_header, self._shuffle = auth_header, shuffle_options
        # A short connect timeout makes an unreachable model fail fast into the rules fallback.
        self._timeout = httpx2.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds))
        self._attempts = attempts

    async def decide(self, request: DecisionInput) -> TriageDecision:
        body = {"model": self._model, "state": request.state_text, "questions": self.questions()}
        started = time.perf_counter()
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_retryable),
                reraise=True,
                wait=wait_exponential_jitter(multiplier=0.5, max=5),
                stop=stop_after_attempt(self._attempts),
            ):
                with attempt:
                    resp = await self._http.post(
                        self._url, json=body, headers=await self._auth_header(), timeout=self._timeout
                    )
                    resp.raise_for_status()
        except httpx2.HTTPError as exc:
            raise DecisionModelError(f"decision model call failed: {type(exc).__name__}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise DecisionModelError("decision model returned non-JSON") from exc
        return parse(payload, int((time.perf_counter() - started) * 1000))

    def questions(self) -> dict[str, Any]:
        if not self._shuffle:
            return QUESTIONS
        q: dict[str, Any] = {k: dict(v) for k, v in QUESTIONS.items()}
        options = list(q["action"]["criteria"].items())
        random.shuffle(options)
        q["action"]["criteria"] = dict(options)
        return q


def parse(payload: dict[str, Any], latency_ms: int) -> TriageDecision:
    """Tolerant parser: missing optional fields become None, so Kev and Microsoft-Decision-1 both work."""
    try:
        return _parse_unchecked(payload, latency_ms)
    except DecisionModelError:
        raise
    except (KeyError, TypeError, ValueError) as exc:  # includes pydantic ValidationError
        raise DecisionModelError(f"unparseable decision response: {exc!r}") from exc


def _parse_unchecked(payload: dict[str, Any], latency_ms: int) -> TriageDecision:
    answers = payload.get("answers") or {}
    action = answers.get("action") or {}
    if "choice" not in action:
        raise DecisionModelError("response has no answers.action.choice")
    probs = {Action(k): float(v) for k, v in (action.get("probabilities") or {}).items() if k in Action}
    chosen = Action(action["choice"])
    sev = answers.get("severity") or {}
    reported = payload.get("latency_ms")
    return TriageDecision(
        source=DecisionSource.MODEL,
        action=chosen,
        action_probabilities=probs,
        # Kev: chance-normalised top probability (ADR 0006). Fall back to the chosen option's probability.
        action_confidence=action.get("confidence", probs.get(chosen)),
        severity_score=sev.get("score"),
        auto_remediable_probability=(answers.get("auto_remediable") or {}).get("noul"),
        noise_probability=(answers.get("is_noise") or {}).get("noul"),
        model=payload.get("model"),
        latency_ms=round(reported) if isinstance(reported, int | float) else latency_ms,  # Kev sends a float
    )
