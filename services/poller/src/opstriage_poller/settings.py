from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from opstriage_shared.settings import BaseServiceSettings


class GrailSettings(BaseModel):
    environment_url: str = "https://example.apps.dynatrace.com"
    oauth_client_id: str = ""
    oauth_client_secret: SecretStr = SecretStr("")
    oauth_scopes: str = (
        "storage:buckets:read storage:events:read storage:entities:read storage:smartscape:read"
    )
    sso_url: str = "https://sso.dynatrace.com/sso/oauth2/token"
    query_timeout_ms: int = Field(default=10_000, ge=1_000)


class ClassicSettings(BaseModel):
    environment_url: str = "https://example.live.dynatrace.com"
    api_token: SecretStr = SecretStr("")
    page_size: int = Field(default=500, ge=1, le=500)


class PollerOptions(BaseModel):
    source: Literal["grail", "classic", "fake"] = "fake"
    interval_seconds: float = Field(default=60, gt=0)
    lookback_hours: int = Field(default=7, ge=1)  # >= 6h Grail refresh interval + poll gap
    active_sweep_every_n_cycles: int = Field(default=10, ge=1)
    intake_url: str = "http://intake-api:8000"
    intake_token: SecretStr = SecretStr("")
    http_timeout_seconds: float = Field(default=30, gt=0)
    fake_fixtures_dir: Path = Path("/app/fixtures/dynatrace/grail")
    grail: GrailSettings = GrailSettings()
    classic: ClassicSettings = ClassicSettings()


class Settings(BaseServiceSettings):
    poller: PollerOptions = PollerOptions()

    @model_validator(mode="after")
    def _check(self) -> Settings:
        p = self.poller
        if p.source == "grail" and not (
            p.grail.oauth_client_id and p.grail.oauth_client_secret.get_secret_value()
        ):
            raise ValueError(
                "POLLER__GRAIL__OAUTH_CLIENT_ID and _SECRET are required for POLLER__SOURCE=grail"
            )
        if p.source == "classic" and not p.classic.api_token.get_secret_value():
            raise ValueError("POLLER__CLASSIC__API_TOKEN is required for POLLER__SOURCE=classic")
        if not p.intake_token.get_secret_value():
            raise ValueError("POLLER__INTAKE_TOKEN is required")
        return self
