from datetime import UTC, datetime, timedelta

import pytest

from opstriage_insights.domain.retention import RetentionPolicy, is_expired

T0 = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
H = timedelta(hours=1)
P = RetentionPolicy()


def test_daily_review_evidence_goes_four_hours_after_the_report_completes() -> None:
    completed = T0 + timedelta(minutes=4)
    assert P.evidence_expires_at(T0, completed) == completed + 4 * H
    assert P.report_expires_at(completed) == completed + 48 * H
    assert P.review_expires_at(T0, completed) == completed + 48 * H


@pytest.mark.parametrize(
    ("completed_after", "expected_after"), [(23 * H, 27 * H), (25 * H, 28 * H), (40 * H, 28 * H)]
)
def test_evidence_never_outlives_28_hours_from_collection(
    completed_after: timedelta, expected_after: timedelta
) -> None:
    assert P.evidence_expires_at(T0, T0 + completed_after) == T0 + expected_after


def test_running_failed_or_abandoned_reviews_purge_at_28_hours() -> None:
    assert P.evidence_expires_at(T0, None) == T0 + 28 * H
    assert P.review_expires_at(T0, None) == T0 + 28 * H


def test_expiry_is_inclusive_of_the_boundary() -> None:
    assert not is_expired(T0 + H, T0)
    assert is_expired(T0, T0)
    assert is_expired(T0, T0 + H)


def test_inconsistent_settings_are_refused() -> None:
    with pytest.raises(ValueError, match="evidence_after_report"):
        RetentionPolicy(evidence_after_report=30 * H)
    with pytest.raises(ValueError, match="report_ttl"):
        RetentionPolicy(report_ttl=2 * H)
