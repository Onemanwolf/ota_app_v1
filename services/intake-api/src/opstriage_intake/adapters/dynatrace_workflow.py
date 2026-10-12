from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from opstriage_contracts import dynatrace
from opstriage_contracts.problems import AlertSource, EntityRef, ProblemEvent, ProblemStatus


class _WfEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str
    name: str | None = None


class WorkflowProblemPayload(BaseModel):
    """Shape of the JSON our Workflow HTTP Request action sends (template in Section 12.6.2).

    The Workflow problem trigger's event() is the Grail dt.davis.problems record, so `severity` follows Grail:
    1 = Critical ... 5 = Informational, null when unset. Evidence is not available on this path.
    """

    model_config = ConfigDict(extra="ignore")
    source: str = "dynatrace-workflow"
    event_id: str
    display_id: str | None = None
    status: str  # ACTIVE | CLOSED
    name: str
    category: str | None = None
    severity: int | None = None
    start: datetime
    end: datetime | None = None
    affected: list[_WfEntity] = Field(default_factory=list)
    root_cause: _WfEntity | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    maintenance: bool = False
    problem_url: str | None = None
    impact_level: str | list[str] | None = None  # dt.davis.impact_level (string or list)
    is_duplicate: bool = False  # dt.davis.is_duplicate

    @field_validator("start", "end", mode="before")
    @classmethod
    def _grail_time(cls, value: object) -> object:
        return dynatrace.grail_time(value)


def to_problem_event(body: dict[str, Any], *, now: datetime | None = None) -> ProblemEvent:
    p = WorkflowProblemPayload.model_validate(body)
    return ProblemEvent(
        source=AlertSource.DYNATRACE_WORKFLOW,
        problem_id=p.event_id,
        display_id=p.display_id or None,
        status=ProblemStatus(p.status.upper()),
        title=p.name,
        category=dynatrace.category(p.category),
        severity=p.severity,
        started_at=p.start,
        ended_at=p.end,
        affected_entities=tuple(
            EntityRef(id=e.id, type=dynatrace.entity_type(e.type), name=e.name) for e in p.affected
        ),
        root_cause_entity=(
            EntityRef(
                id=p.root_cause.id, type=dynatrace.entity_type(p.root_cause.type), name=p.root_cause.name
            )
            if p.root_cause
            else None
        ),
        tags={**p.tags, **({"maintenance": "true"} if p.maintenance else {})},
        observed_at=now or datetime.now(UTC),
        problem_url=p.problem_url,
        impact_levels=dynatrace.impact_levels(p.impact_level),
        is_duplicate=p.is_duplicate,
    )
