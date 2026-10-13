"""Shared helpers for health-review tests: fixture sources at the fixtures' anchor time."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from opstriage_insights.adapters.cluster_config import fixture_clusters
from opstriage_insights.adapters.fixtures import (
    FixtureLogSource,
    FixtureMetricSource,
    FixtureProblemSource,
    FixtureStore,
)
from opstriage_insights.application.collect import CollectEvidence
from opstriage_insights.domain.redaction import Redactor

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "insights"
ANCHOR = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
STORE = FixtureStore(FIXTURES, lambda: ANCHOR, shift_to_now=False)
SCOPES = fixture_clusters(STORE)


def collector() -> CollectEvidence:
    return CollectEvidence(
        FixtureLogSource(STORE), FixtureMetricSource(STORE), FixtureProblemSource(STORE), Redactor()
    )


async def digest_for(cluster: str):  # type: ignore[no-untyped-def]
    from opstriage_insights.adapters.guard import KeywordGuard
    from opstriage_insights.application.collect import BuildDigest

    return await BuildDigest(KeywordGuard())(await collector()(SCOPES[cluster], ANCHOR))
