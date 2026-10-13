"""Clusters to review, from config/insights.<env>.yaml; the fake source adds the fixture clusters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from opstriage_insights.adapters.fixtures import FixtureStore
from opstriage_insights.domain.model import ClusterScope


class _Cluster(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    namespace: str = ""
    containers: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clusters: list[_Cluster] = Field(default_factory=list)


def load_clusters(path: Path) -> dict[str, ClusterScope]:
    if not path.is_file():
        return {}
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = _Config.model_validate(raw)
    return {
        c.name: ClusterScope(c.name, c.namespace, tuple(c.containers), tuple(c.entity_ids))
        for c in cfg.clusters
    }


def fixture_clusters(store: FixtureStore) -> dict[str, ClusterScope]:
    out = {}
    for name in store.clusters():
        scope = store.scenario(name).get("scope", {})
        out[name] = ClusterScope(
            name,
            scope.get("namespace", ""),
            tuple(scope.get("containers", ())),
            tuple(scope.get("entity_ids", ())),
        )
    return out
