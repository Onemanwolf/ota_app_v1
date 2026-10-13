from datetime import UTC, datetime

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_triage.domain.policy import Policy

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def _event(
    status: ProblemStatus = ProblemStatus.ACTIVE, display_id: str | None = "P-26101201"
) -> ProblemEvent:
    return ProblemEvent(
        source=AlertSource.SIMULATOR,
        problem_id="p",
        display_id=display_id,
        status=status,
        title="t",
        started_at=NOW,
        observed_at=NOW,
        problem_url="https://dt.example/p",
    )


def test_root_cause_link_only_when_configured_and_the_problem_is_open() -> None:
    links = Policy.notification_links(_event(), "http://agent/v1/runs/1", "http://insights:8120/")
    assert links["Analyse root cause"] == "http://insights:8120/ui/rca?display_id=P-26101201"
    assert (
        links["Triage run"] == "http://agent/v1/runs/1"
        and links["Dynatrace problem"] == "https://dt.example/p"
    )
    assert "Analyse root cause" not in Policy.notification_links(_event(), "r")
    assert "Analyse root cause" not in Policy.notification_links(
        _event(ProblemStatus.CLOSED), "r", "http://i"
    )
    assert "Analyse root cause" not in Policy.notification_links(_event(display_id=None), "r", "http://i")
