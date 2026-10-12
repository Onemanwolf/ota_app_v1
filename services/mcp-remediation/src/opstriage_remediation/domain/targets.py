"""Restart target registry (spec 12.5.2). The registry is the only source of restartable targets."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opstriage_contracts.problems import EntityRef
from opstriage_contracts.tools import TARGET_ID_PATTERN, TargetSummary

Autonomy = Literal["notify_only", "approval_required", "autonomous"]
_AUTONOMY_ORDER: dict[str, int] = {"notify_only": 0, "approval_required": 1, "autonomous": 2}


class ExecutorKind(StrEnum):
    LOCAL_DOCKER = "local_docker"
    AZURE_RUN_COMMAND = "azure_run_command"
    ARC_RUN_COMMAND = "arc_run_command"
    SSH = "ssh"


def lower_autonomy(a: Autonomy, b: Autonomy) -> Autonomy:
    return a if _AUTONOMY_ORDER[a] <= _AUTONOMY_ORDER[b] else b


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetMatch(_Model):
    entity_ids: tuple[str, ...] = ()
    host_entity_ids: tuple[str, ...] = ()
    host_names: tuple[str, ...] = ()
    container_names: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _not_empty(self) -> TargetMatch:
        host_rule = (self.host_entity_ids or self.host_names) and self.container_names
        if not self.entity_ids and not host_rule:
            raise ValueError("a target needs match.entity_ids, or a host match plus container_names")
        return self


class ExecutorConfig(_Model):
    kind: ExecutorKind
    container: str = Field(pattern=TARGET_ID_PATTERN)
    resource_id: str | None = None  # azure_run_command / arc_run_command
    host: str | None = None  # ssh


class TargetPolicy(_Model):
    autonomy: Autonomy = "notify_only"
    cooldown_minutes: int = Field(default=15, ge=0)
    max_restarts_per_day: int = Field(default=3, ge=0)
    restart_timeout_seconds: int = Field(default=30, ge=1, le=300)


class RestartTarget(_Model):
    id: str = Field(pattern=TARGET_ID_PATTERN)
    description: str
    match: TargetMatch
    executor: ExecutorConfig
    policy: TargetPolicy = TargetPolicy()

    def matches(self, entities: Iterable[EntityRef]) -> bool:
        ents = list(entities)
        if any(e.id in self.match.entity_ids for e in ents):
            return True
        hosts = [e for e in ents if e.type.lower() == "host"]
        host_hit = any(h.id in self.match.host_entity_ids or (h.name in self.match.host_names) for h in hosts)
        container_hit = any(
            e.type.lower() == "container" and e.name in self.match.container_names for e in ents
        )
        return host_hit and container_hit

    def summary(self, max_autonomy: Autonomy) -> TargetSummary:
        return TargetSummary(
            target_id=self.id,
            description=self.description,
            executor=self.executor.kind.value,
            autonomy=lower_autonomy(self.policy.autonomy, max_autonomy),
            container=self.executor.container,
            host_entity_ids=self.match.host_entity_ids,
        )


class TargetRegistry(_Model):
    version: Literal[1] = 1
    targets: tuple[RestartTarget, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> TargetRegistry:
        ids = [t.id for t in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate target ids in registry")
        return self

    def get(self, target_id: str) -> RestartTarget | None:
        return next((t for t in self.targets if t.id == target_id), None)

    def match(self, entities: Iterable[EntityRef]) -> tuple[RestartTarget | None, bool]:
        """(target, ambiguous). More than one match means ambiguity: no target is returned."""
        ents = list(entities)
        hits = [t for t in self.targets if t.matches(ents)]
        if len(hits) == 1:
            return hits[0], False
        return None, len(hits) > 1

    def executor_kinds(self) -> set[ExecutorKind]:
        return {t.executor.kind for t in self.targets}
