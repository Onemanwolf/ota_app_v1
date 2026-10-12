from __future__ import annotations

from uuid import UUID, uuid4


class UuidGenerator:
    def new(self) -> UUID:
        return uuid4()
