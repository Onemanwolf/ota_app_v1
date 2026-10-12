from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from opstriage_contracts.problems import ProblemEvent
from opstriage_intake.domain.alert import AlertState, AlertView

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "dynatrace"


class InMemoryInbox:
    """Fake InboxRepository with the same dedup semantics as the SQL adapter."""

    def __init__(self) -> None:
        self.rows: dict[UUID, tuple[ProblemEvent, dict[str, Any] | None]] = {}
        self.keys: set[str] = set()
        self.notified = 0

    async def add_if_new(self, *, alert_id: UUID, event: ProblemEvent, raw: dict[str, Any] | None) -> bool:
        keys = {event.dedup_key} | (
            {f"d:{event.display_id}:{event.status.value}"} if event.display_id else set()
        )
        if keys & self.keys:
            return False
        self.keys |= keys
        self.rows[alert_id] = (event, raw)
        return True

    async def notify_new(self) -> None:
        self.notified += 1

    async def get(self, alert_id: UUID) -> AlertView | None:
        if alert_id not in self.rows:
            return None
        event, _ = self.rows[alert_id]
        return AlertView(
            alert_id,
            event.problem_id,
            event.display_id,
            event.status.value,
            event.source.value,
            AlertState.PENDING,
            0,
            datetime.now(UTC),
            None,
        )


@pytest.fixture
def inbox() -> InMemoryInbox:
    return InMemoryInbox()


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
