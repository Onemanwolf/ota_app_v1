"""Teams card snapshot (spec 12.5.5). Regenerate after an intended change: UPDATE_SNAPSHOTS=1 uv run pytest ..."""

import json
import os
from pathlib import Path

from opstriage_contracts.tools import NotificationKind, NotificationRequest, Severity
from opstriage_notification.domain.teams_card import build_teams_message

SNAPSHOT = Path(__file__).parent / "snapshots" / "problem_opened_card.json"


def test_problem_opened_card_matches_snapshot() -> None:
    request = NotificationRequest(
        idempotency_key="run-1:notify:opened",
        kind=NotificationKind.PROBLEM_OPENED,
        severity=Severity.CRITICAL,
        title="[P-2610121] Container demo-orders-api unavailable",
        summary='Dynatrace reports AVAILABILITY problem "Container demo-orders-api unavailable" (ACTIVE).',
        facts={
            "Problem": "P-2610121",
            "Decision": "restart_container (model, confidence 0.16)",
            "Restart target": "demo-orders-api (autonomy approval_required)",
        },
        links={
            "Triage run": "http://localhost:8010/v1/runs/run-1",
            "Dynatrace problem": "https://example.apps.dynatrace.com/ui/apps/dynatrace.davis.problems/problem/P-2610121",
        },
    )
    rendered = json.dumps(build_teams_message(request), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") or not SNAPSHOT.exists():
        SNAPSHOT.write_text(rendered)
    assert rendered == SNAPSHOT.read_text()
