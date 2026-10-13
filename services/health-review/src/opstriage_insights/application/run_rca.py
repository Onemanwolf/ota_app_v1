"""Root-cause analysis for one Dynatrace problem (OTA-APP-002 3.4)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from opstriage_insights.domain.model import ClusterScope, ProblemRecord
from opstriage_insights.ports.sources import ProblemSource

LOOK_BACK = timedelta(hours=2)  # the window opens this long before the problem started
MAX_WINDOW = timedelta(hours=22)  # the 5-minute series cover 24 h ending at the window's end


class RcaError(LookupError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class RcaPlan:
    scope: ClusterScope
    window: tuple[datetime, datetime]
    subject: dict[str, Any]


def resolve_cluster(
    problem: ProblemRecord, clusters: Mapping[str, ClusterScope], hint: str | None, requested: str | None
) -> ClusterScope:
    """The requested cluster, else the source's hint, else the one configured cluster the problem touches."""
    if requested:
        if requested not in clusters:
            raise RcaError(404, "unknown cluster")
        return clusters[requested]
    if hint and hint in clusters:
        return clusters[hint]
    touched = [c for c in clusters.values() if set(c.entity_ids) & set(problem.entity_ids)]
    if len(touched) == 1:
        return touched[0]
    if not touched:
        raise RcaError(422, "the problem's entities are not in any configured cluster; pass `cluster`")
    raise RcaError(409, "the problem touches several clusters; pass `cluster`")


class PlanRca:
    def __init__(
        self,
        problems: ProblemSource,
        clusters: Mapping[str, ClusterScope],
        clock: Callable[[], datetime],
        look_back: timedelta = LOOK_BACK,
    ) -> None:
        self._problems, self._clusters, self._clock, self._look_back = problems, clusters, clock, look_back

    async def __call__(self, display_id: str, cluster: str | None = None) -> RcaPlan:
        found = await self._problems.find(display_id)
        if found is None:
            raise RcaError(404, "unknown problem")
        p = found.problem
        scope = resolve_cluster(p, self._clusters, found.cluster_hint, cluster)
        end = min(p.end or self._clock(), self._clock())
        start = max(p.start - self._look_back, end - MAX_WINDOW)
        subject = {
            "display_id": p.display_id,
            "problem_id": p.problem_id,
            "title": p.title,
            "status": p.status,
            "problem_start": p.start.isoformat(),
            "window": {"start": start.isoformat(), "end": end.isoformat()},
        }
        return RcaPlan(scope, (start, end), subject)
