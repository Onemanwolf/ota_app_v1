"""Serialize collected (already redacted) evidence for insights.evidence rows, one bundle per source."""

from __future__ import annotations

from typing import Any

from opstriage_insights.application.collect import CollectedEvidence
from opstriage_insights.domain.model import MetricSeries


def _series(s: MetricSeries) -> dict[str, Any]:
    return {
        "metric": s.metric,
        "dimensions": s.dimensions,
        "start": s.start.isoformat(),
        "interval_s": s.interval.total_seconds(),
        "values": list(s.values),
    }


def serialize(ev: CollectedEvidence) -> dict[str, dict[str, Any]]:
    return {
        "logs": {
            "records": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "status": r.status,
                    "loglevel": r.loglevel,
                    "content": r.content,
                    "entity_id": r.entity_id,
                    "pod": r.pod,
                    "host": r.host,
                }
                for r in ev.logs
            ],
            "redactions": dict(ev.redactions),
        },
        "metrics": {
            "day": [_series(s) for group in ev.day_series.values() for s in group],
            "month": [_series(s) for group in ev.month_series.values() for s in group],
        },
        "problems": {
            "records": [
                {
                    "problem_id": p.problem_id,
                    "display_id": p.display_id,
                    "title": p.title,
                    "category": p.category,
                    "status": p.status,
                    "start": p.start.isoformat(),
                    "end": p.end.isoformat() if p.end else None,
                    "entity_ids": list(p.entity_ids),
                }
                for p in ev.problems
            ]
        },
    }
