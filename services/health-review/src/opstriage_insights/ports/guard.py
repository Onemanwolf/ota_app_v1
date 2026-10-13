"""Prompt-injection screening of untrusted log text (OTA-APP-002 D7)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class GuardUnavailable(RuntimeError):
    """The guard could not screen the texts. Callers fail closed: no log text reaches the model."""


class Guard(Protocol):
    async def flags(self, texts: Sequence[str]) -> list[bool]:
        """True for each text the guard classifies as an injection attempt. Raises GuardUnavailable."""
        ...
