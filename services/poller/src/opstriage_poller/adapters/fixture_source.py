"""FixtureProblemSource (POLLER__SOURCE=fake): replays Grail-format records from JSON files.

Fixture timestamps are fixed, so `last_update` is rebased to the moment the source was created: the
first cycle sees every record as changed, later cycles see nothing new (only the active sweep repeats them,
and the intake answers with duplicates). That is what a quiet tenant looks like.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from opstriage_contracts.problems import ProblemEvent, ProblemStatus
from opstriage_poller.adapters.grail import map_record


class FixtureProblemSource:
    def __init__(self, fixtures_dir: Path, loaded_at: datetime) -> None:
        self._dir, self._loaded_at = fixtures_dir, loaded_at

    async def fetch_changed(self, since: datetime, lookback: timedelta) -> list[ProblemEvent]:
        return [e for e in await self._events() if e.observed_at > since]

    async def fetch_active(self) -> list[ProblemEvent]:
        return [e for e in await self._events() if e.status is ProblemStatus.ACTIVE]

    async def _events(self) -> list[ProblemEvent]:
        records = await asyncio.to_thread(self._read)
        return [map_record({**r, "last_update": self._loaded_at}) for r in records]

    def _read(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted(self._dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            records += data if isinstance(data, list) else [data]
        return records
