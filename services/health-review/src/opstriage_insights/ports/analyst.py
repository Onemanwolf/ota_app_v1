"""The analyst: any model that turns a digest into findings (OTA-APP-002 section 6). Model-agnostic port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from opstriage_insights.domain.findings import AnalystOutput
from opstriage_insights.ports.store import ReviewKind


class AnalystUnavailable(RuntimeError):
    """The model could not be reached, or kept returning output that does not match the schema."""


@dataclass(frozen=True, slots=True)
class AnalystResult:
    output: AnalystOutput
    model: str
    latency_ms: int
    calls: int


class Analyst(Protocol):
    async def analyse(self, digest_text: str, kind: ReviewKind) -> AnalystResult: ...
