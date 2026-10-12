"""Anti-corruption adapter for the classic custom-integration webhook (spec 12.2.2 and 12.6.3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opstriage_contracts.problems import AlertSource, EntityRef, ProblemEvent, ProblemStatus

_STATE = {"OPEN": ProblemStatus.ACTIVE, "RESOLVED": ProblemStatus.CLOSED, "MERGED": ProblemStatus.CLOSED}


class _EntityId(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str


class _ClassicEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entityId: _EntityId
    name: str | None = None

    def to_ref(self) -> EntityRef:
        return EntityRef(id=self.entityId.id, type=self.entityId.type.lower(), name=self.name)


class _ProblemDetails(BaseModel):
    """The subset of `{ProblemDetailsJSONv2}` (Problems API v2 shape) that we use."""

    model_config = ConfigDict(extra="ignore")
    startTime: int | None = None
    endTime: int | None = None
    severityLevel: str | None = None
    affectedEntities: list[_ClassicEntity] = Field(default_factory=list)
    rootCauseEntity: _ClassicEntity | None = None


class ClassicProblemPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pid: str = Field(min_length=1)  # {PID}
    problem_id: str | None = None  # {ProblemID}, the user-facing display ID
    state: str  # {State}: OPEN | RESOLVED | MERGED
    title: str
    severity: str | None = None  # {ProblemSeverity}: AVAILABILITY, ERROR, ... (a category)
    url: str | None = None
    tags: str | None = None  # {Tags}: "key:value, flag"
    details: _ProblemDetails = Field(default_factory=_ProblemDetails)


def _ms(value: int | None) -> datetime | None:
    if value is None or value < 0:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _parse_tags(raw: str | None) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        key, sep, value = item.partition(":")
        tags[key.strip()] = value.strip() if sep else "true"
    return tags


def to_problem_event(body: dict[str, Any], *, now: datetime | None = None) -> ProblemEvent:
    p = ClassicProblemPayload.model_validate(body)
    state = p.state.upper()
    if state not in _STATE:
        raise ValueError(f"unknown classic problem state: {p.state!r}")
    observed = now or datetime.now(UTC)
    tags = _parse_tags(p.tags)
    if state == "MERGED":
        tags["merged"] = "true"
    return ProblemEvent(
        source=AlertSource.DYNATRACE_CLASSIC_WEBHOOK,
        problem_id=p.pid,
        display_id=p.problem_id or None,
        status=_STATE[state],
        title=p.title,
        category=p.details.severityLevel or p.severity,
        started_at=_ms(p.details.startTime) or observed,
        ended_at=_ms(p.details.endTime),
        affected_entities=tuple(e.to_ref() for e in p.details.affectedEntities),
        root_cause_entity=p.details.rootCauseEntity.to_ref() if p.details.rootCauseEntity else None,
        tags=tags,
        observed_at=observed,
        problem_url=p.url,
    )
