from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Action(StrEnum):
    NOTIFY_ONLY = "notify_only"
    RESTART_CONTAINER = "restart_container"
    OPEN_TICKET = "open_ticket"
    ESCALATE_HUMAN = "escalate_human"
    SUPPRESS = "suppress"
    CANNOT_TELL = "cannot_tell"  # explicit abstain option


class DecisionSource(StrEnum):
    RULES = "rules"
    MODEL = "model"


class TriageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    source: DecisionSource
    action: Action
    action_probabilities: dict[Action, float] = Field(default_factory=dict)
    action_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    severity_score: float | None = None  # 0..(levels-1), probability-weighted
    auto_remediable_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    noise_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    rule_id: str | None = None  # set when a deterministic rule decided
    model: str | None = None  # provider model name for audit
    latency_ms: int | None = None
