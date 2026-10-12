from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AlertSource(StrEnum):
    DYNATRACE_WORKFLOW = "dynatrace.workflow"
    DYNATRACE_CLASSIC_WEBHOOK = "dynatrace.classic_webhook"
    DYNATRACE_POLLER_GRAIL = "dynatrace.poller.grail"
    DYNATRACE_POLLER_CLASSIC = "dynatrace.poller.classic"
    SIMULATOR = "simulator"


class ProblemStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class EntityRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str  # e.g. HOST-017198AD253CBD63, CONTAINER-...
    type: str  # host, container, process, service, ...
    name: str | None = None


class ProblemEvent(BaseModel):
    """Canonical, source-agnostic problem observation. Anti-corruption adapters produce it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    source: AlertSource
    problem_id: str = Field(description="Grail event.id, classic {PID} or Problems API v2 problemId")
    display_id: str | None = Field(default=None, description="User-facing ID such as P-2307288")
    status: ProblemStatus
    title: str
    category: str | None = None  # AVAILABILITY, ERROR, SLOWDOWN, RESOURCE, CUSTOM_ALERT, ...
    severity: int | None = Field(default=None, ge=1, le=5)  # Grail event.severity (1 to 5) when present
    started_at: datetime
    ended_at: datetime | None = None
    affected_entities: tuple[EntityRef, ...] = ()
    root_cause_entity: EntityRef | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    observed_at: datetime  # when the source produced this observation
    problem_url: str | None = None

    @property
    def dedup_key(self) -> str:
        return f"{self.problem_id}:{self.status.value}"
