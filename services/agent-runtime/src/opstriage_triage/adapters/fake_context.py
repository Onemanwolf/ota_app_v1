from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from opstriage_contracts.problems import ProblemEvent
from opstriage_contracts.tools import ResolveTargetResult
from opstriage_triage.domain.context import ProblemContext
from opstriage_triage.ports import InboxPort

_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class FakeProblemContextSource:
    """Stands in for Grail locally.

    Logs come from `<fixtures_dir>/<display_id>.json`, else `<tags.context_fixture>.json`, else
    `default.json`, so tests can inject log text (for example a prompt-injection line) without fixing the
    problem IDs. `get_problem` answers from the inbox: the newest event received for the problem, which makes
    a CLOSED webhook visible to the remediation guard.
    """

    def __init__(self, fixtures_dir: Path, inbox: InboxPort, max_log_lines: int) -> None:
        self._dir, self._inbox, self._max = fixtures_dir, inbox, max_log_lines

    async def get_context(self, event: ProblemEvent, resolved: ResolveTargetResult) -> ProblemContext:
        logs = await asyncio.to_thread(self._logs, event.display_id, event.tags.get("context_fixture"))
        return ProblemContext(
            event=event,
            target=resolved.target,
            recent_restarts_24h=resolved.recent_restarts_24h,
            logs=tuple(dict.fromkeys(logs))[: self._max],
        )

    async def get_problem(self, problem_id: str) -> ProblemEvent | None:
        return await self._inbox.latest_event(problem_id)

    def _logs(self, *names: str | None) -> list[str]:
        for name in (*names, "default"):
            if not name or not _SAFE.match(name):
                continue
            path = self._dir / f"{name}.json"
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                return [str(line) for line in data.get("logs", [])]
        return []
