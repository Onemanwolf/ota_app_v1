from typing import Any
from uuid import UUID

import pytest
from insights_testkit import ANCHOR, SCOPES, collector

from opstriage_insights.adapters.evidence_json import serialize
from opstriage_insights.adapters.guard import KeywordGuard
from opstriage_insights.adapters.stores import MemoryReviewStore
from opstriage_insights.application.collect import BuildDigest, PrepareReview
from opstriage_insights.domain.retention import RetentionPolicy

RID = UUID("00000000-0000-0000-0000-000000000042")


def _prepare(store: MemoryReviewStore, collect: Any = None) -> PrepareReview:
    return PrepareReview(
        store,
        collect or collector(),
        BuildDigest(KeywordGuard()),
        RetentionPolicy(),
        lambda: ANCHOR,
        lambda: RID,
        serialize,
    )


async def test_review_row_and_redacted_evidence_are_stored_with_28h_expiry() -> None:
    store = MemoryReviewStore()
    prepared = await _prepare(store)(SCOPES["planted-ssl-one-client"])
    assert prepared.review_id == RID and prepared.digest.cluster == "planted-ssl-one-client"
    review = store.reviews[RID]
    assert review["status"] == "running" and review["kind"] == "review"
    assert (review["expires_at"] - ANCHOR).total_seconds() == 28 * 3600
    assert sorted(e["source"] for e in store.evidence) == ["logs", "metrics", "problems"]
    assert all((e["expires_at"] - ANCHOR).total_seconds() == 28 * 3600 for e in store.evidence)
    logs = next(e["bundle"] for e in store.evidence if e["source"] == "logs")
    assert logs["redactions"].get("ipv4", 0) > 0
    assert all(".x" in r["content"] or "/10." not in r["content"] for r in logs["records"])


async def test_rca_subject_marks_the_kind() -> None:
    store = MemoryReviewStore()
    await _prepare(store)(SCOPES["healthy-steady"], {"display_id": "P-1"})
    assert store.reviews[RID]["kind"] == "rca" and store.reviews[RID]["subject"] == {"display_id": "P-1"}


async def test_a_collection_failure_marks_the_review_failed() -> None:
    class Broken:
        async def __call__(self, *_: Any) -> None:
            raise TimeoutError

    store = MemoryReviewStore()
    with pytest.raises(TimeoutError):
        await _prepare(store, Broken())(SCOPES["healthy-steady"])
    assert store.reviews[RID]["status"] == "failed" and store.reviews[RID]["error"] == "TimeoutError"
    assert store.evidence == []
