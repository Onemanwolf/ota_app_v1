from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator

from opstriage_shared.settings import BaseServiceSettings, ServiceAuthSettings


class TeamsSettings(BaseModel):
    provider: Literal["workflows_webhook", "log"] = "log"
    webhook_url: SecretStr | None = None  # contains a signature: a secret, never logged
    card_version: str = "1.5"


class EmailSettings(BaseModel):
    provider: Literal["smtp", "acs", "log"] = "log"
    from_address: str = "opstriage@local.test"
    default_recipients: list[str] = Field(default_factory=list)
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    acs_endpoint: str | None = None


class NotificationOptions(BaseModel):
    teams: TeamsSettings = TeamsSettings()
    email: EmailSettings = EmailSettings()
    rate_limit_per_minute: int = Field(default=20, ge=1)


class NotificationServerSettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104 (container)
    port: int = 8101
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost:*", "mcp-notification:*"])


class Settings(BaseServiceSettings):
    notification: NotificationOptions = NotificationOptions()
    server: NotificationServerSettings = NotificationServerSettings()
    auth: ServiceAuthSettings = ServiceAuthSettings()

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if (
            self.notification.teams.provider == "workflows_webhook"
            and self.notification.teams.webhook_url is None
        ):
            raise ValueError(
                "NOTIFICATION__TEAMS__WEBHOOK_URL is required for the workflows_webhook provider"
            )
        if self.notification.email.provider == "smtp" and self.app_env != "local":
            raise ValueError("the smtp email provider (no TLS, no auth) is only permitted when APP_ENV=local")
        if self.notification.email.provider == "acs":
            raise ValueError("the acs email provider arrives with the Azure deployment (OTA-AZ-001)")
        return self
