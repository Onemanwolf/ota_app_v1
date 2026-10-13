from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr

from opstriage_shared.settings import BaseServiceSettings, Platform


class IntakeOptions(BaseModel):
    webhook_tokens: list[SecretStr] = Field(default_factory=list)  # Dynatrace Workflows / classic webhooks
    service_tokens: list[SecretStr] = Field(default_factory=list)  # poller, tests, Postman
    enable_dev_endpoints: bool = False
    fixtures_dir: Path = Path("/app/fixtures/dynatrace")
    max_payload_bytes: int = Field(default=262_144, ge=1_024)
    inbox_consumer_role: str | None = None


class IntakeServerSettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104 (container)
    port: int = 8000


class Settings(BaseServiceSettings):
    intake: IntakeOptions = IntakeOptions()
    server: IntakeServerSettings = IntakeServerSettings()

    @classmethod
    def platform_defaults(cls, platform: Platform) -> dict[str, Any]:
        return {"intake": {"enable_dev_endpoints": False}} if platform == "azure" else {}

    def platform_violations(self) -> list[str]:
        return ["INTAKE__ENABLE_DEV_ENDPOINTS must be false"] if self.intake.enable_dev_endpoints else []

    @property
    def dev_endpoints_enabled(self) -> bool:
        return self.intake.enable_dev_endpoints and self.app_env == "local"
