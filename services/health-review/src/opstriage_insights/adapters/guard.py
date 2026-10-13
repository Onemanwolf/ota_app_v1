"""Guard adapters: the DeBERTa prompt-injection service on the Spark (port 8004) and a keyword fake.

Service API (observed 2026-10-13): POST /check_batch {"texts": [...]} -> [{"label", "score", "injection"}].
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import httpx2

from opstriage_insights.ports.guard import GuardUnavailable

BATCH = 32


class HttpGuard:
    def __init__(self, http: httpx2.AsyncClient, base_url: str, timeout_seconds: float = 20) -> None:
        self._http, self._url = http, f"{base_url.rstrip('/')}/check_batch"
        self._timeout = httpx2.Timeout(timeout_seconds, connect=min(3.0, timeout_seconds))

    async def flags(self, texts: Sequence[str]) -> list[bool]:
        out: list[bool] = []
        for i in range(0, len(texts), BATCH):
            chunk = list(texts[i : i + BATCH])
            try:
                resp = await self._http.post(self._url, json={"texts": chunk}, timeout=self._timeout)
                resp.raise_for_status()
                results = resp.json()
                if not isinstance(results, list) or len(results) != len(chunk):
                    raise GuardUnavailable("guard returned a malformed response")
                out.extend(bool(r.get("injection")) for r in results)
            except (httpx2.HTTPError, ValueError, AttributeError) as exc:
                raise GuardUnavailable(f"guard call failed: {type(exc).__name__}") from exc
        return out


_INJECTION = re.compile(
    r"ignore (all )?(previous|prior|above) instructions|disregard (the|your) (rules|instructions)|"
    r"you are now|system (note|prompt) to the (ai|assistant|model)|act as (an? )?(admin|system)",
    re.IGNORECASE,
)


class KeywordGuard:
    """Fake for tests and offline runs: flags well-known injection phrasing only."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available

    async def flags(self, texts: Sequence[str]) -> list[bool]:
        if not self._available:
            raise GuardUnavailable("keyword guard configured as unavailable")
        return [bool(_INJECTION.search(t)) for t in texts]
