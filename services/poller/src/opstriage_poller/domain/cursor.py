from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Cursor:
    source: str
    high_water_mark: datetime  # only moves forward after intake accepted the whole batch
    last_sweep_at: datetime | None
