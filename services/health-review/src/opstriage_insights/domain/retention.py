"""Retention rules (OTA-APP-002 section 4, user decision 2026-10-13).

Pure date arithmetic; the purge job (5.8) deletes rows whose `expires_at` has passed.

- Collected evidence: min(collected_at + 28h, completed_at + 4h). Without a successful completion (running,
  failed or abandoned), 28h after collection.
- Reports: completed_at + 48h.
- A review row lives as long as its longest-lived child: its report once completed, otherwise its evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    report_ttl: timedelta = timedelta(hours=48)
    evidence_max_age: timedelta = timedelta(hours=28)
    evidence_after_report: timedelta = timedelta(hours=4)

    def __post_init__(self) -> None:
        if self.evidence_after_report > self.evidence_max_age:
            raise ValueError("evidence_after_report must not exceed evidence_max_age")
        if self.report_ttl < self.evidence_after_report:
            raise ValueError("report_ttl must be at least evidence_after_report")

    def evidence_expires_at(self, collected_at: datetime, completed_at: datetime | None) -> datetime:
        cap = collected_at + self.evidence_max_age
        if completed_at is None:
            return cap
        return min(cap, completed_at + self.evidence_after_report)

    def report_expires_at(self, completed_at: datetime) -> datetime:
        return completed_at + self.report_ttl

    def review_expires_at(self, started_at: datetime, completed_at: datetime | None) -> datetime:
        if completed_at is None:
            return started_at + self.evidence_max_age
        return self.report_expires_at(completed_at)


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return expires_at <= now
