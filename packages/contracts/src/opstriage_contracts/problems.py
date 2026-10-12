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
    id: str  # e.g. HOST-017198AD253CBD63, PROCESS_GROUP_INSTANCE-...
    type: str  # lowercase canonical: host, container, process_group_instance, service, ...
    name: str | None = None


ImpactLevel = Literal["APPLICATION", "ENVIRONMENT", "INFRASTRUCTURE", "SERVICE", "SYNTHETIC"]
EvidenceType = Literal["EVENT", "METRIC", "TRANSACTIONAL", "MAINTENANCE_WINDOW", "AVAILABILITY_EVIDENCE"]


class Evidence(BaseModel):
    """One piece of Davis evidence (Problems API v2 `evidenceDetails`, or Grail `dt.davis.events` joined via
    `dt.davis.event_ids`). Added in schema 1.1; see reports/Dynatrace Kafka triage data models.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_type: EvidenceType
    display_name: str
    entity: EntityRef | None = None
    root_cause_relevant: bool | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None  # None while still active
    event_type: str | None = None  # EVENT: e.g. PGI_UNAVAILABLE, OSI_LOW_DISK_SPACE
    event_id: str | None = None
    metric_id: str | None = None  # METRIC: e.g. kafka.server.ReplicaManager.UnderReplicatedPartitions
    unit: str | None = None
    value_before: float | None = None  # valueBeforeChangePoint
    value_after: float | None = None  # valueAfterChangePoint


class ProblemEvent(BaseModel):
    """Canonical, source-agnostic problem observation. Anti-corruption adapters produce it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0", "1.1"] = "1.1"  # 1.1 adds impact_levels, evidence, is_duplicate
    source: AlertSource
    problem_id: str = Field(description="Grail event.id, classic {PID} or Problems API v2 problemId")
    display_id: str | None = Field(default=None, description="User-facing ID such as P-2307288")
    status: ProblemStatus
    title: str
    category: str | None = (
        None  # Grail names: AVAILABILITY, ERROR, SLOWDOWN, RESOURCE_CONTENTION, CUSTOM_ALERT, ...
    )
    severity: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Grail event.severity, ITIL-aligned: 1 = Critical, 2 = High, 3 = Medium, 4 = Low, "
        "5 = Informational. None when Dynatrace sets none; always None from Problems API v2 and the "
        "classic webhook.",
    )
    started_at: datetime
    ended_at: datetime | None = None
    affected_entities: tuple[EntityRef, ...] = ()
    root_cause_entity: EntityRef | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    observed_at: datetime  # when the source produced this observation
    problem_url: str | None = None
    impact_levels: tuple[ImpactLevel, ...] = ()  # SERVICE == v2 SERVICES == Grail "Services"
    evidence: tuple[Evidence, ...] = ()  # only Problems API v2 and {ProblemDetailsJSONv2} carry it
    is_duplicate: bool = False  # Grail dt.davis.is_duplicate (merged into another problem)

    @property
    def dedup_key(self) -> str:
        return f"{self.problem_id}:{self.status.value}"
