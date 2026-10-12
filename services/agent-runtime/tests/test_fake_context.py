from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opstriage_contracts.problems import AlertSource, ProblemEvent, ProblemStatus
from opstriage_contracts.tools import ResolveTargetResult
from opstriage_triage.adapters.fake_context import FakeProblemContextSource

CONTEXT = Path(__file__).resolve().parents[3] / "fixtures" / "context"
NOW = datetime(2026, 10, 12, tzinfo=UTC)


def event(**kw: Any) -> ProblemEvent:
    return ProblemEvent.model_validate(
        {
            "source": AlertSource.SIMULATOR,
            "problem_id": "p",
            "status": ProblemStatus.ACTIVE,
            "title": "t",
            "started_at": NOW,
            "observed_at": NOW,
        }
        | kw
    )


async def test_logs_by_tag_then_default_and_unsafe_names_ignored() -> None:
    source = FakeProblemContextSource(CONTEXT, inbox=None, max_log_lines=50)  # type: ignore[arg-type]
    none = ResolveTargetResult(target=None)
    injected = await source.get_context(event(tags={"context_fixture": "injection"}), none)
    assert any("IGNORE PREVIOUS INSTRUCTIONS" in line for line in injected.logs)
    default = await source.get_context(event(display_id="P-unknown"), none)
    assert default.logs and "OOMKilled" in default.logs[0]
    traversal = await source.get_context(event(tags={"context_fixture": "../../etc/passwd"}), none)
    assert traversal.logs == default.logs
