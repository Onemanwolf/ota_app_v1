"""Settings for health-review (OTA-APP-002 section 9). Each value has an INSIGHTS__* entry in .env.example."""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from opstriage_dynatrace.grail import GrailSettings
from opstriage_shared.settings import BaseServiceSettings

DEV_SESSION_SECRET = "change-me-dev-only"  # noqa: S105  (placeholder; refused outside app_env=local)


class InsightsGrailSettings(GrailSettings):
    """Read-only scopes for logs, metrics, problems and entities (OTA-APP-002 section 8)."""

    oauth_scopes: str = (
        "storage:buckets:read storage:logs:read storage:metrics:read storage:events:read "
        "storage:entities:read storage:smartscape:read"
    )


class ScheduleSettings(BaseModel):
    daily_at: time = time(6, 0)
    timezone: str = "UTC"


class CollectSettings(BaseModel):
    log_window: timedelta = timedelta(hours=24)
    trend_window: timedelta = timedelta(days=30)
    max_log_records: int = Field(default=50_000, ge=100)


class DigestSettings(BaseModel):
    max_tokens: int = Field(default=12_000, ge=1_000)


class AnalystSettings(BaseModel):
    base_url: str = "http://host.docker.internal:18001"
    path: str = "/v1/chat/completions"
    model: str = "qwen3.8-flash-next"
    timeout_seconds: float = Field(default=180, gt=0)
    concurrency: int = Field(default=1, ge=1, le=4)  # Qwen is shared with triage (D5)


class GuardSettings(BaseModel):
    base_url: str = "http://host.docker.internal:18004"


class RetentionSettings(BaseModel):
    report_ttl: timedelta = timedelta(hours=48)
    evidence_max_age: timedelta = timedelta(hours=28)
    evidence_after_report: timedelta = timedelta(hours=4)
    purge_interval: timedelta = timedelta(minutes=15)


class TicketSettings(BaseModel):
    enabled: bool = False


class WebSettings(BaseModel):
    base_url: str = "http://localhost:8120"  # used in Teams card links
    session_secret: SecretStr = SecretStr(DEV_SESSION_SECRET)
    session_ttl: timedelta = timedelta(hours=8)
    allow_download: bool = False  # a downloaded copy outlives the 48h retention (open question 10)
    trusted_proxy_header: str = ""  # e.g. X-Auth-Request-Email behind an auth proxy; empty locally
    sparkline_points: int = Field(default=96, ge=8, le=500)


class InsightsOptions(BaseModel):
    source: Literal["fake", "grail"] = "fake"
    fixtures_dir: Path = Path("/app/fixtures/insights")
    service_tokens: list[SecretStr] = Field(default_factory=list)  # JSON API bearer tokens
    grail: InsightsGrailSettings = InsightsGrailSettings()
    schedule: ScheduleSettings = ScheduleSettings()
    collect: CollectSettings = CollectSettings()
    digest: DigestSettings = DigestSettings()
    analyst: AnalystSettings = AnalystSettings()
    guard: GuardSettings = GuardSettings()
    retention: RetentionSettings = RetentionSettings()
    tickets: TicketSettings = TicketSettings()
    web: WebSettings = WebSettings()


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104 (container)
    port: int = 8120


class Settings(BaseServiceSettings):
    insights: InsightsOptions = InsightsOptions()
    server: ServerSettings = ServerSettings()

    @model_validator(mode="after")
    def _check(self) -> Settings:
        i = self.insights
        if i.source == "grail" and not i.grail.configured:
            raise ValueError(
                "INSIGHTS__GRAIL__OAUTH_CLIENT_ID and _SECRET are required for INSIGHTS__SOURCE=grail"
            )
        r = i.retention
        if r.evidence_after_report > r.evidence_max_age:
            raise ValueError("INSIGHTS__RETENTION__EVIDENCE_AFTER_REPORT must not exceed EVIDENCE_MAX_AGE")
        if r.report_ttl < r.evidence_after_report:
            raise ValueError("INSIGHTS__RETENTION__REPORT_TTL must be at least EVIDENCE_AFTER_REPORT")
        if self.app_env != "local" and i.web.session_secret.get_secret_value() in ("", DEV_SESSION_SECRET):
            raise ValueError("INSIGHTS__WEB__SESSION_SECRET must be set outside app_env=local")
        return self
