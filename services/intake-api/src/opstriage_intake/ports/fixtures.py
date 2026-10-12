from __future__ import annotations

from typing import Any, Protocol


class FixtureNotFound(LookupError):
    pass


class FixtureLoader(Protocol):
    async def load(self, kind: str, name: str) -> dict[str, Any]:
        """Raise FixtureNotFound for unknown fixtures."""
        ...
