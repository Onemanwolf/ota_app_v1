from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from opstriage_intake.ports.fixtures import FixtureNotFound

_SAFE = re.compile(r"^[a-z0-9_]{1,80}$")


class DirectoryFixtureLoader:
    """Reads `<root>/<kind>/<name>.json`. Names are validated, so no path can escape the root."""

    def __init__(self, root: Path, kinds: frozenset[str] = frozenset({"workflow", "classic"})) -> None:
        self._root, self._kinds = root, kinds

    async def load(self, kind: str, name: str) -> dict[str, Any]:
        if kind not in self._kinds or not _SAFE.match(name):
            raise FixtureNotFound(f"{kind}/{name}")
        path = self._root / kind / f"{name}.json"
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError as exc:
            raise FixtureNotFound(f"{kind}/{name}") from exc
        data = json.loads(text)
        if not isinstance(data, dict):
            raise FixtureNotFound(f"{kind}/{name} is not a JSON object")
        return data
