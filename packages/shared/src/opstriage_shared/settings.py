from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["local", "test", "dev", "prod"]


class DatabaseSettings(BaseModel):
    dsn: SecretStr = Field(description="postgresql://user:pass@host:5432/db (password mode)")
    auth_mode: Literal["password", "entra"] = "password"
    entra_client_id: str | None = None  # user-assigned managed identity client ID (entra mode)
    pool_min_size: int = 1
    pool_max_size: int = 10
    pool_max_lifetime_seconds: int = 3000  # keep below Entra token lifetime


class TelemetrySettings(BaseModel):
    enabled: bool = False
    service_namespace: str = "opstriage"
    # Standard OTEL_EXPORTER_OTLP_* variables configure the exporter itself.


class ServiceAuthSettings(BaseModel):
    """Bearer tokens for service-to-service calls (v1). Upgrade path: Entra ID tokens."""

    inbound_tokens: list[SecretStr] = Field(default_factory=list)  # accepted; supports rotation


class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),  # later file wins; real env vars beat both
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: AppEnv = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    db: DatabaseSettings
    telemetry: TelemetrySettings = TelemetrySettings()
